"""
La identidad que se radica: rol del documento, nombres que son frases, reglas
fijas de identidad y revisor obligatorio antes de radicar.

Los casos reproducen, con datos inventados, los fallos de la prueba de punta a
punta sobre correos reales: el siniestro como cedula, la cedula del medico que
firma, el NIT del demandante, el codigo de barras del OCR, la frase del relato
como nombre, la cedula con ceros de relleno, el firmante que es el afectado,
la ESE afectada sin tipo NIT y la ESE que escribe sin quedar como remitente.
"""
import pytest

from models.email import EmailAddress, EmailBody, EmailMessage
from pipeline import compuerta
from pipeline.compuerta import Veredicto
from pipeline.extraccion import (
    Hallazgo,
    extraer_nombre_afectado,
    motivo_de_frase,
    motivo_por_el_que_no_es_un_nombre,
)
from pipeline.modelo import Llamada, ModeloFalso, Turno
from pipeline.orquestador import (
    Orquestador,
    Resultado,
    _reparos_de_identidad,
    extractos_del_valor,
)
from pipeline.revisor import Revision, Reparo as ReparoRevisor


def _h(campo, valor, metodo="m", confianza=0.9):
    return Hallazgo(campo=campo, valor=valor, evidencia="e",
                    confianza=confianza, metodo=metodo)


def _base(**extra):
    hallazgos = {
        "nombre_afectado": _h("nombre_afectado", "PEDRO RUIZ MEJIA"),
        "numero_documento_afectado": _h("numero_documento_afectado", "41234567"),
        "tipo_documento_afectado": _h("tipo_documento_afectado", 1),
        "nombre_remitente": _h("nombre_remitente", "Ana Lopez"),
        "tipo_solicitud": _h("tipo_solicitud", 16),
        "tipo_remitente": _h("tipo_remitente", 2),
    }
    hallazgos.update({c: _h(c, v) for c, v in extra.items()})
    return hallazgos


def _codigos(dictamen):
    return {r.codigo for r in dictamen.reparos}


# ── Rol del documento ──

def test_el_numero_de_siniestro_no_es_la_cedula():
    fuentes = ("Yo, PEDRO RUIZ MEJIA, mayor de edad, identificado con el "
               "siniestro no. 60741136. Solicito la calificacion.")
    d = compuerta.validar(_base(numero_documento_afectado="60741136"),
                          fuentes=fuentes)
    assert "DOCUMENTO_CON_ROTULO_AJENO" in _codigos(d)
    assert d.veredicto is Veredicto.RECHAZADO


def test_el_registro_medico_es_de_quien_firma():
    fuentes = ("Paciente PEDRO RUIZ MEJIA. Concepto de medicina laboral.\n"
               "LUIS GOMEZ TORO CC No. 1018477636 / RM 1018477636 "
               "Medicina del trabajo")
    d = compuerta.validar(_base(numero_documento_afectado="1018477636"),
                          fuentes=fuentes)
    assert "DOCUMENTO_DE_UN_PROFESIONAL" in _codigos(d)


def test_el_nit_del_demandante_no_es_del_demandado():
    fuentes = ("PROCESO EJECUTIVO DEMANDANTE COOPERATIVA X LTDA (NIT. 890904894) "
               "DEMANDADO PEDRO RUIZ MEJIA (C.C. 41.234.567)")
    d = compuerta.validar(_base(numero_documento_afectado="890904894",
                                tipo_documento_afectado=10), fuentes=fuentes)
    assert "DOCUMENTO_DE_OTRA_PERSONA_NOMBRADA" in _codigos(d)


def test_el_codigo_de_barras_del_ocr_no_es_un_documento():
    fuentes = ("CEDULA DE CIUDADANIA\nPEDRO RUIZ\n"
               "A-1500109-45 1 1502 18 02967 031980 01 139378293 s aa")
    d = compuerta.validar(_base(numero_documento_afectado="139378293"),
                          fuentes=fuentes)
    assert "DOCUMENTO_EN_UNA_TIRA_DE_CIFRAS" in _codigos(d)


def test_la_cedula_junto_al_nombre_del_afectado_vale_aunque_haya_un_siniestro():
    fuentes = ("Siniestro no. 41234567 del trabajador PEDRO RUIZ MEJIA, "
               "identificado con C.C. 41.234.567")
    d = compuerta.validar(_base(), fuentes=fuentes)
    assert not _codigos(d) & {"DOCUMENTO_CON_ROTULO_AJENO",
                              "DOCUMENTO_DE_OTRA_PERSONA_NOMBRADA"}


def test_un_documento_sin_rotulo_no_se_rechaza():
    d = compuerta.validar(_base(), fuentes="PEDRO RUIZ MEJIA 41234567 pago")
    assert d.veredicto is not Veredicto.RECHAZADO


def test_el_documento_del_remitente_de_un_profesional_se_retira_sin_bloquear():
    fuentes = ("Ana Lopez. LUIS GOMEZ TORO CC 79111222 / RM 79111222. "
               "PEDRO RUIZ MEJIA C.C. 41234567")
    d = compuerta.validar(_base(numero_documento_remitente="79111222",
                                tipo_documento_remitente=1), fuentes=fuentes)
    assert d.veredicto is Veredicto.CORREGIDO
    assert d.correcciones["numero_documento_remitente"] == ""


def test_un_hermano_no_es_el_titular_por_compartir_apellidos():
    texto = compuerta._texto_para_roles(
        "Yo, KAREN MARGARITA BRAVO MARTINEZ, identificada con cedula de "
        "ciudadania No. 50.989.588, hermana de LEIMER MIGUEL BRAVO MARTINEZ")
    assert not compuerta.documento_junto_al_nombre(
        "50989588", "Leimer Miguel Bravo Martinez", texto)
    assert compuerta.documento_junto_al_nombre(
        "50989588", "Karen Margarita Bravo Martinez", texto)


# ── Ceros de relleno ──

def test_la_cedula_con_ceros_de_relleno_se_envia_sin_ellos():
    d = compuerta.validar(_base(numero_documento_afectado="000000019314619"),
                          fuentes="Pedro Ruiz Mejia C.C. 000000019314619")
    assert "LONGITUD_IMPROBABLE" not in _codigos(d)
    assert d.correcciones["numero_documento_afectado"] == "19314619"


def test_el_pasaporte_conserva_sus_ceros():
    assert compuerta.sin_ceros_a_la_izquierda("0123456", 5) in ("0123456", "123456")
    assert compuerta.sin_ceros_a_la_izquierda("00412", None) == "412"


# ── Nombres que son frases ──

@pytest.mark.parametrize("frase", [
    "ENCONTRABA LABORANDO REALIZANDO", "ANTES DE INICIAR LABORES",
    "VALORADO POR ORTOPEDIA", "No Retenido Nuevo", "ACTUALIZACION DE INFORMACION",
    "NO APLICA", "el senor que firma la carta de abajo mas adelante",
])
def test_una_frase_no_es_un_nombre(frase):
    assert motivo_de_frase(frase)
    assert motivo_por_el_que_no_es_un_nombre(frase)


@pytest.mark.parametrize("nombre", [
    "Fernando Armando Ruiz", "Maria del Carmen de la Hoz", "Concepcion Perez",
    "JORGE A. BARRERA NOSSA", "Orlando Hernandez Prado", "CORREA RESTREPO JESUS ALBERTO",
])
def test_un_nombre_real_no_es_una_frase(nombre):
    assert motivo_de_frase(nombre) == ""


@pytest.mark.parametrize("nombre", ["RODILLA DERECHA", "NORBERTO MENA Fecha"])
def test_el_vocabulario_nuevo_no_pasa_por_nombre(nombre):
    assert motivo_por_el_que_no_es_un_nombre(nombre)


def test_la_etiqueta_seguida_de_una_frase_no_da_nombre():
    h, _ = extraer_nombre_afectado(
        "INFORME DE ACCIDENTE QUE RELATA TRABAJADOR QUE SE ENCONTRABA LABORANDO "
        "REALIZANDO LABORES EN LA PLANTA")
    assert h is None or "LABORANDO" not in h.valor


def test_el_nombre_de_una_regla_debil_va_a_confirmar():
    h, _ = extraer_nombre_afectado("", asunto="Solicitud PEDRO RUIZ MEJIA")
    assert h is not None and h.confianza < 0.86
    assert h.a_confirmar


def test_el_nombre_de_una_regla_firme_no_va_a_confirmar():
    h, _ = extraer_nombre_afectado(
        "el trabajador PEDRO RUIZ MEJIA identificado con C.C. 41234567")
    assert h is not None and h.confianza >= 0.86
    assert not h.a_confirmar


# ── Reglas fijas de identidad ──

def _heredar(hallazgos, fuentes, correo=""):
    res = Resultado(email_id="x")
    Orquestador._heredar_datos_del_afectado(
        hallazgos, res, fuentes=fuentes, correo_remitente=correo)
    return res


def test_la_cedula_del_firmante_que_es_el_afectado_sustituye_al_siniestro():
    hallazgos = _base(numero_documento_afectado="60741136", tipo_remitente=3,
                      numero_documento_remitente="15920033",
                      tipo_documento_remitente=1)
    hallazgos["numero_documento_afectado"].metodo = "cercania_al_nombre"
    hallazgos["numero_documento_remitente"].metodo = "autoidentificacion_del_remitente"
    fuentes = ("Yo, PEDRO RUIZ MEJIA, mayor de edad, identificado con la cedula "
               "de ciudadania No. 15.920.033, con el siniestro no. 60741136.")
    res = _heredar(hallazgos, fuentes)
    assert hallazgos["numero_documento_afectado"].valor == "15920033"
    assert any("documento_del_firmante" in a for a in res.avisos)


def test_el_firmante_hereda_su_cedula_al_afectado_sin_documento():
    hallazgos = _base(nombre_remitente="Consultores Premium",
                      numero_documento_remitente="98479454",
                      tipo_documento_remitente=1)
    del hallazgos["numero_documento_afectado"]
    fuentes = "Atentamente,\nPEDRO RUIZ MEJIA\nC.C. 98.479.454\nAlcalde Municipal"
    _heredar(hallazgos, fuentes)
    assert hallazgos["numero_documento_afectado"].valor == "98479454"
    assert hallazgos["tipo_documento_afectado"].valor == 1


def test_no_se_hereda_si_el_afectado_ya_tiene_su_cedula_junto_al_nombre():
    hallazgos = _base(numero_documento_remitente="91481220",
                      tipo_documento_remitente=1)
    fuentes = ("Yo, RAUL HERNANDEZ SAAVEDRA, identificado con cedula No. "
               "91.481.220, empleador del senor PEDRO RUIZ MEJIA, C.C. 41.234.567")
    _heredar(hallazgos, fuentes)
    assert hallazgos["numero_documento_afectado"].valor == "41234567"


def test_la_ese_afectada_va_con_su_nit_y_tipo_nit():
    hallazgos = _base(nombre_afectado="CAMILO RUEDA DE VILLANUEVA",
                      numero_documento_afectado="804005182",
                      tipo_documento_afectado=10)
    fuentes = ("CARTERA - ESE CENTRO DE SALUD CAMILO RUEDA DE VILLANUEVA NIT "
               "804005182")
    _heredar(hallazgos, fuentes)
    assert hallazgos["nombre_afectado"].valor == (
        "ESE CENTRO DE SALUD CAMILO RUEDA DE VILLANUEVA")
    assert hallazgos["tipo_documento_afectado"].valor == 10
    d = compuerta.validar(hallazgos, fuentes=fuentes)
    assert "NIT_DE_ENTIDAD_EN_PERSONA" not in _codigos(d)


def test_la_entidad_del_dominio_es_el_remitente_que_faltaba():
    hallazgos = _base()
    del hallazgos["nombre_remitente"]
    fuentes = ("INFORMACION DEL PRESTADOR Nombre ESE CARMEN EMILIA OSPINA NIT X "
               "813005265 7 Direccion")
    _heredar(hallazgos, fuentes, correo="fac-granjas@esecarmenemiliaospina.gov.co")
    assert hallazgos["nombre_remitente"].valor == "ESE CARMEN EMILIA OSPINA"
    assert hallazgos["tipo_documento_remitente"].valor == 10


def test_un_dominio_publico_no_nombra_a_nadie():
    assert compuerta.entidad_del_dominio(
        "ana@gmail.com", "GMAIL COM NIT 900123456") is None


# ── Revisor obligatorio ──

def test_los_reparos_del_revisor_ignoran_un_cambiar_al_mismo_valor():
    revision = Revision(reparos={
        "nombre_afectado": ReparoRevisor("nombre_afectado", "CAMBIAR", "Pedro Ruiz Mejia"),
        "numero_documento_afectado": ReparoRevisor(
            "numero_documento_afectado", "SIN RESPALDO", detalle="es un siniestro"),
        "tipo_solicitud": ReparoRevisor("tipo_solicitud", "CAMBIAR", "5"),
    })
    reparos = _reparos_de_identidad(revision, {
        "nombre_afectado": "PEDRO RUIZ MEJIA", "numero_documento_afectado": "60741136"})
    assert set(reparos) == {"numero_documento_afectado"}
    assert "siniestro" in reparos["numero_documento_afectado"]


def test_los_extractos_muestran_donde_aparece_el_numero():
    texto = compuerta._texto_para_roles("bla bla siniestro no. 60.741.136 de PEDRO")
    assert "siniestro" in extractos_del_valor("60741136", texto)[0]


CUERPO = (
    "Buen dia,\n"
    "Solicito el pago de la incapacidad del trabajador PEDRO RUIZ MEJIA "
    "identificado con C.C. 41234567, Sexo: Masculino.\n"
    "Celular 3105551234.\nCordialmente,\nAna Lopez"
)


def _email(cuerpo=CUERPO):
    return EmailMessage(
        id="correo-revisado", subject="Pago de incapacidad",
        body=EmailBody(content=cuerpo),
        from_address=EmailAddress(name="Ana Lopez", address="ana@empresa.com"),
        received_datetime="2026-08-24T13:30:00Z",
    )


def _reporta(campo, valor):
    return Llamada("reportar_hallazgo", {
        "campo": campo, "valor": str(valor), "evidencia": "cuerpo del correo",
        "confianza": 0.9})


class _Radicador:
    def __init__(self):
        self.payloads = []

    async def radicar(self, email, hallazgos, archivos=None, cuerpo_limpio=""):
        self.payloads.append(dict(hallazgos))
        return "ENT1"


@pytest.mark.asyncio
async def test_el_revisor_corre_aunque_el_agente_no_lo_pida(tmp_path):
    modelo = ModeloFalso(turnos=[
        Turno(llamadas=[_reporta("tipo_solicitud", 16), _reporta("tipo_remitente", 2)]),
        Turno(texto="No se encontro la direccion del afectado."),
        Turno(texto="nombre_afectado: OK\nnumero_documento_afectado: OK\nREVISADO"),
    ])
    orq = Orquestador(modelo=modelo, radicador=_Radicador(),
                      base_workspace=str(tmp_path))
    r = await orq.procesar(_email())
    assert r.accion == "radicado", r.motivo_manual
    assert r.revision.get("llamadas") == 1
    assert "Comprueba estos valores" in modelo.recibidos[-1]["mensajes"][0]["contenido"]
    assert any("dio por buena" in a for a in r.avisos)


@pytest.mark.asyncio
async def test_lo_que_el_revisor_rechaza_vuelve_al_agente_y_sin_correccion_va_a_manual(
        tmp_path):
    modelo = ModeloFalso(turnos=[
        Turno(llamadas=[_reporta("tipo_solicitud", 16), _reporta("tipo_remitente", 2)]),
        Turno(texto="No se encontro la direccion del afectado."),
        Turno(texto="nombre_afectado: SIN RESPALDO | es un trozo de frase\n"
                    "REVISADO"),
        # La ronda de correccion: el agente no encuentra otro nombre.
        Turno(texto="No se identifica el nombre del afectado."),
    ])
    orq = Orquestador(modelo=modelo, radicador=_Radicador(),
                      base_workspace=str(tmp_path))
    # Sin la cedula rotulada junto al nombre, el SIN RESPALDO del revisor vale.
    r = await orq.procesar(_email(CUERPO.replace("identificado con C.C. 41234567",
                                                  "con numero 41234567")))
    assert r.accion == "manual"
    assert r.motivo_manual == "SIN_NOMBRE_AFECTADO"
    assert "nombre_afectado" in r.revision["reparos"]
    assert any("el revisor rechazo" in a for a in r.avisos)


@pytest.mark.asyncio
async def test_el_nombre_con_su_cedula_rotulada_no_lo_quita_el_revisor(tmp_path):
    modelo = ModeloFalso(turnos=[
        Turno(llamadas=[_reporta("tipo_solicitud", 16), _reporta("tipo_remitente", 2)]),
        Turno(texto="No se encontro la direccion del afectado."),
        Turno(texto="nombre_afectado: SIN RESPALDO | el correo habla de otra persona\n"
                    "REVISADO"),
    ])
    orq = Orquestador(modelo=modelo, radicador=_Radicador(),
                      base_workspace=str(tmp_path))
    r = await orq.procesar(_email())
    assert r.accion == "radicado", (r.motivo_manual, r.avisos)
    assert r.hallazgos["nombre_afectado"].valor == "PEDRO RUIZ MEJIA"
    assert any("su documento rotulado: se mantiene" in a for a in r.avisos)


@pytest.mark.asyncio
async def test_lo_corregido_al_valor_que_propone_el_revisor_no_se_revisa_otra_vez(
        tmp_path):
    modelo = ModeloFalso(turnos=[
        Turno(llamadas=[_reporta("tipo_solicitud", 16), _reporta("tipo_remitente", 2)]),
        Turno(texto="No se encontro la direccion del afectado."),
        Turno(texto="nombre_afectado: CAMBIAR a PEDRO R. MEJIA | firma\n"
                    "REVISADO"),
        Turno(llamadas=[_reporta("nombre_afectado", "PEDRO R. MEJIA")]),
    ])
    orq = Orquestador(modelo=modelo, radicador=_Radicador(),
                      base_workspace=str(tmp_path))
    r = await orq.procesar(_email())
    assert r.accion == "radicado", (r.motivo_manual, r.avisos)
    assert r.hallazgos["nombre_afectado"].valor == "PEDRO R. MEJIA"
    assert r.revision["llamadas"] == 1


@pytest.mark.asyncio
async def test_si_el_revisor_falla_se_sigue_con_la_compuerta(tmp_path, monkeypatch):
    from pipeline import revisor as revisor_mod

    def _falla(*a, **k):
        raise RuntimeError("cuota agotada")

    monkeypatch.setattr(revisor_mod, "revisar", _falla)
    modelo = ModeloFalso(turnos=[
        Turno(llamadas=[_reporta("tipo_solicitud", 16), _reporta("tipo_remitente", 2)]),
        Turno(texto="No se encontro la direccion del afectado."),
    ])
    orq = Orquestador(modelo=modelo, radicador=_Radicador(),
                      base_workspace=str(tmp_path))
    r = await orq.procesar(_email())
    assert r.accion == "radicado", r.motivo_manual
    assert any("el revisor no se pudo ejecutar" in a for a in r.avisos)


def test_en_el_remitente_el_revisor_solo_aconseja_y_el_documento_rotulado_se_mantiene():
    revision = Revision(reparos={
        "tipo_remitente": ReparoRevisor("tipo_remitente", "CAMBIAR", "1"),
        "nombre_remitente": ReparoRevisor("nombre_remitente", "CAMBIAR", "Positiva"),
        "numero_documento_afectado": ReparoRevisor(
            "numero_documento_afectado", "SIN RESPALDO", detalle="dudoso"),
    })
    texto = compuerta._texto_para_roles(
        "el trabajador PEDRO RUIZ MEJIA identificado con C.C. 41.234.567")
    avisos = []
    reparos = _reparos_de_identidad(revision, {
        "tipo_remitente": 3, "nombre_remitente": "Ana Lopez",
        "nombre_afectado": "PEDRO RUIZ MEJIA",
        "numero_documento_afectado": "41234567"}, texto, avisos)
    assert reparos == {}
    assert len(avisos) == 3


def test_el_nombre_del_remitente_que_no_es_un_nombre_si_vuelve_al_agente():
    revision = Revision(reparos={"nombre_remitente": ReparoRevisor(
        "nombre_remitente", "CAMBIAR", "ESE CARMEN EMILIA OSPINA")})
    reparos = _reparos_de_identidad(
        revision, {"nombre_remitente": "urgencias de la planta mas abajo"})
    assert set(reparos) == {"nombre_remitente"}


def test_el_revisor_no_deja_vacio_el_tipo_de_solicitud():
    """Duda entre dos tipos no es ausencia: SIN RESPALDO solo avisa."""
    avisos = []
    revision = Revision(reparos={"tipo_solicitud": ReparoRevisor(
        "tipo_solicitud", "SIN RESPALDO", detalle="no pide eso")})
    assert _reparos_de_identidad(revision, {"tipo_solicitud": 24}, "", avisos) == {}
    assert avisos
    revision = Revision(reparos={"tipo_solicitud": ReparoRevisor(
        "tipo_solicitud", "CAMBIAR", "14")})
    assert set(_reparos_de_identidad(revision, {"tipo_solicitud": 24})) == {
        "tipo_solicitud"}


# ── Buzones y areas que pasaban como remitente, y "empresa" delante ──

def test_un_buzon_con_la_misma_palabra_dos_veces_no_es_nadie():
    from utils.generic_filter import motivo_por_el_que_no_identifica_a_nadie
    assert motivo_por_el_que_no_identifica_a_nadie("urgencias urgencias")
    assert not motivo_por_el_que_no_identifica_a_nadie("Maria Maria Lopez")


def test_un_area_abreviada_de_una_entidad_no_es_la_entidad():
    from utils.generic_filter import motivo_por_el_que_no_identifica_a_nadie
    assert motivo_por_el_que_no_identifica_a_nadie("Dpto. Cartera E.S.E.")
    assert not motivo_por_el_que_no_identifica_a_nadie("Hospital San Jose ESE")


def test_se_quita_empresa_delante_de_una_razon_social():
    from pipeline.compuerta import _revisar_entidad_con_antepuesto
    for nombre, esperado in [("empresa SUPERMERCADOS OLIVAN", "SUPERMERCADOS OLIVAN"),
                             ("la empresa ACME SAS", "ACME SAS")]:
        reparos, correcciones = [], {}
        _revisar_entidad_con_antepuesto("nombre_afectado", nombre, reparos, correcciones)
        assert correcciones == {"nombre_afectado": esperado}
        assert not reparos[0].bloqueante


def test_empresa_que_abre_el_nombre_se_respeta():
    from pipeline.compuerta import _revisar_entidad_con_antepuesto
    reparos, correcciones = [], {}
    _revisar_entidad_con_antepuesto(
        "nombre_afectado", "Empresa de Energia del Pacifico SA ESP", reparos, correcciones)
    assert correcciones == {} and reparos == []
