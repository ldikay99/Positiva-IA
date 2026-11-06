"""Lo rechazado por la compuerta vuelve al agente con su motivo."""
from pipeline.compuerta import Dictamen, Reparo, Veredicto
from pipeline.correccion import (
    MOTIVO_RECHAZADO,
    campos_a_corregir,
    extraccion_para_corregir,
)
from pipeline.extraccion import (
    MOTIVOS_DE_AUSENCIA_CONSTATADA,
    Extraccion,
    Hallazgo,
    NoResuelto,
)


def _h(campo, valor, a_confirmar=""):
    return Hallazgo(campo=campo, valor=valor, evidencia="e", confianza=0.9,
                    metodo="m", a_confirmar=a_confirmar)


def _dictamen(*reparos):
    return Dictamen(veredicto=Veredicto.RECHAZADO, reparos=list(reparos))


def test_devuelve_los_campos_rechazados_con_su_motivo():
    det = Extraccion(hallazgos={}, no_resueltos={})
    d = _dictamen(
        Reparo("nombre_remitente", "NO_PARECE_UN_NOMBRE", "es un area"),
        Reparo("numero_documento_afectado", "NIT_DE_ENTIDAD", "es el NIT"),
    )
    a = campos_a_corregir(d, det)
    assert set(a) == {"nombre_remitente", "numero_documento_afectado"}
    assert "es un area" in a["nombre_remitente"]


def test_no_devuelve_lo_que_el_correo_no_trae():
    ausencia = next(iter(MOTIVOS_DE_AUSENCIA_CONSTATADA))
    det = Extraccion(hallazgos={}, no_resueltos={
        "numero_documento_afectado": NoResuelto(
            "numero_documento_afectado", ausencia, "no hay documento")})
    d = _dictamen(Reparo("numero_documento_afectado",
                         "FALTA_CAMPO_OBLIGATORIO", "falta"))
    assert campos_a_corregir(d, det) == {}


def test_no_devuelve_reparos_no_bloqueantes_ni_campos_ajenos():
    det = Extraccion(hallazgos={}, no_resueltos={})
    d = _dictamen(
        Reparo("telefono_afectado", "TELEFONO_DUDOSO", "x", bloqueante=False),
        Reparo("oficina", "SIN_OFICINA", "x"),
    )
    assert campos_a_corregir(d, det) == {}


def test_la_tarea_de_correccion_pide_solo_lo_rechazado_y_dice_lo_propuesto():
    det = Extraccion(hallazgos={}, no_resueltos={}, contexto="ASUNTO: x")
    hallazgos = {
        "nombre_remitente": _h("nombre_remitente", "Gestion del Talento Humano"),
        "nombre_afectado": _h("nombre_afectado", "Ana Ruiz", a_confirmar="dudoso"),
    }
    corr = extraccion_para_corregir(
        det, hallazgos, {"nombre_remitente": "NO_PARECE_UN_NOMBRE: es un area"})
    assert corr.campos_para_el_agente == ["nombre_remitente"]
    nr = corr.no_resueltos["nombre_remitente"]
    assert nr.motivo == MOTIVO_RECHAZADO
    assert "Gestion del Talento Humano" in nr.detalle
    assert "es un area" in nr.detalle
    # Lo aceptado no se vuelve a preguntar.
    assert corr.hallazgos["nombre_afectado"].a_confirmar == ""
    assert corr.contexto == "ASUNTO: x"
    assert "Gestion del Talento Humano" in corr.resumen_para_ia()
