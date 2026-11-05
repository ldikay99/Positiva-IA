"""
Herramientas MCP, ejercitadas sin modelo.

Cubren el contrato de cada herramienta y, sobre todo, el de sus errores: un
fallo tiene que llegar como error con motivo, nunca como respuesta vacia.
"""
import pytest

from pipeline.materializar import materializar
from servidor_mcp.herramientas import (
    CAMPOS_REPORTABLES,
    HERRAMIENTAS,
    MAX_LINEAS_POR_LECTURA,
    Contexto,
    _digito_verificacion_nit,
    invocar,
)


@pytest.fixture
def ctx(tmp_path):
    ws = materializar(
        id_correo="correo-mcp",
        asunto="Solicitud de incapacidad",
        cuerpo_limpio=(
            "Buen dia, solicito el pago de la incapacidad del trabajador\n"
            "JUAN PEREZ identificado con CC 41234567.\n"
            "Telefono de contacto 310 555 1234.\n"
            "Cordialmente, Maria Gomez"
        ),
        adjuntos=[("soporte.txt", b"Incapacidad numero 900123 del trabajador")],
        base=str(tmp_path), temporal=False,
    )
    contexto = Contexto(workspace=ws)
    yield contexto
    ws.cerrar()


# ── Contrato general ──

def test_una_herramienta_inexistente_lo_dice_y_lista_las_validas(ctx):
    r = invocar(ctx, "borrar_todo")
    assert r.ok is False
    assert "no existe la herramienta" in r.error
    assert "listar_documentos" in r.error


def test_ninguna_herramienta_tiene_efectos_secundarios():
    """
    Regla que no se negocia: la IA propone, el pipeline ejecuta. Si una
    herramienta pudiera radicar o mover correo, una instruccion inyectada en un
    PDF podria provocar una accion real.
    """
    prohibidas = ("radicar", "etiquetar", "mover", "enviar", "reenviar",
                  "eliminar", "borrar", "escribir", "guardar", "actualizar")
    for nombre in HERRAMIENTAS:
        assert not any(p in nombre for p in prohibidas), (
            f"la herramienta '{nombre}' sugiere un efecto secundario"
        )


def test_argumentos_incorrectos_dan_error_util(ctx):
    r = invocar(ctx, "leer_documento", {"argumento_inventado": 1})
    assert r.ok is False
    assert "argumentos incorrectos" in r.error


# ── Listar e inventario ──

def test_listar_no_devuelve_contenido(ctx):
    """Listar debe ser barato: el agente decide que leer sin pagar por leerlo."""
    r = invocar(ctx, "listar_documentos")
    assert r.ok is True
    assert len(r.datos) >= 2
    for doc in r.datos:
        assert "caracteres" in doc
        assert "contenido" not in doc


# ── Leer con topes ──

def test_leer_un_fragmento(ctx):
    r = invocar(ctx, "leer_documento", {"documento": "correo", "desde": 0, "lineas": 5})
    assert r.ok is True
    assert "41234567" in r.datos["contenido"]


def test_leer_documento_inexistente_sugiere_los_disponibles(ctx):
    """El error enumera los documentos que si existen en el workspace."""
    r = invocar(ctx, "leer_documento", {"documento": "no-existe"})
    assert r.ok is False
    assert "no existe el documento" in r.error
    assert "correo" in r.error


def test_no_se_puede_pedir_el_documento_completo_de_un_tiron(ctx):
    """
    El tope existe para que el agente no pueda deshacer el diseño: leer todo de
    una vez es exactamente el problema que se quiere eliminar.
    """
    r = invocar(ctx, "leer_documento",
                {"documento": "correo", "lineas": MAX_LINEAS_POR_LECTURA + 1})
    assert r.ok is False
    assert "no puede exceder" in r.error
    assert "buscar_en_documentos" in r.error, "el error debe guiar a la alternativa"


@pytest.mark.parametrize("args,fragmento_error", [
    ({"documento": ""}, "falta el argumento"),
    ({"documento": "correo", "lineas": 0}, "mayor que cero"),
    ({"documento": "correo", "desde": -5}, "no puede ser negativo"),
])
def test_valida_los_argumentos_de_lectura(ctx, args, fragmento_error):
    r = invocar(ctx, "leer_documento", args)
    assert r.ok is False
    assert fragmento_error in r.error


# ── Buscar ──

def test_buscar_ubica_el_dato_con_su_documento_y_linea(ctx):
    r = invocar(ctx, "buscar_en_documentos", {"patron": r"\d{8}"})
    assert r.ok is True
    assert r.datos["total"] >= 1
    primera = r.datos["coincidencias"][0]
    assert "documento" in primera and "linea" in primera and "contexto" in primera


def test_buscar_encuentra_en_los_adjuntos_tambien(ctx):
    r = invocar(ctx, "buscar_en_documentos", {"patron": "900123"})
    assert r.ok is True
    assert r.datos["total"] >= 1


def test_un_patron_invalido_explica_como_corregirlo(ctx):
    r = invocar(ctx, "buscar_en_documentos", {"patron": "[sin cerrar"})
    assert r.ok is False
    assert "expresion regular valida" in r.error


def test_buscar_sin_patron_falla_claro(ctx):
    r = invocar(ctx, "buscar_en_documentos", {"patron": ""})
    assert r.ok is False
    assert "falta el argumento" in r.error


# ── Catalogos ──

def test_consultar_un_catalogo_devuelve_codigos_y_descripciones(ctx):
    r = invocar(ctx, "consultar_catalogo", {"nombre": "tipo_documento"})
    assert r.ok is True
    codigos = {v["codigo"] for v in r.datos["valores"]}
    assert 1 in codigos and 10 in codigos


def test_un_catalogo_inexistente_lista_los_validos(ctx):
    r = invocar(ctx, "consultar_catalogo", {"nombre": "inventado"})
    assert r.ok is False
    assert "tipo_documento" in r.error


# ── Validacion de documento: aritmetica, no opinion ──

def test_el_digito_de_verificacion_se_calcula_paso_a_paso():
    """
    Verificacion manual del algoritmo de la DIAN sobre 900123456.

    Digitos de derecha a izquierda por sus pesos:
      6x3=18  5x7=35  4x13=52  3x17=51  2x19=38
      1x23=23  0x29=0  0x37=0  9x41=369
    Suma = 586.  586 mod 11 = 3.  DV = 11 - 3 = 8.

    Se deja el desarrollo escrito porque una constante suelta no se puede
    revisar: quien lea esto puede recalcularlo sin confiar en el numero.
    """
    assert _digito_verificacion_nit("900123456") == 8


@pytest.mark.parametrize("base", [
    "900123456", "800197268", "899999998", "830001338", "811000000",
])
def test_el_digito_de_verificacion_cumple_sus_propiedades(base):
    """
    No se afirman valores memorizados que no se puedan verificar: se afirma que
    el resultado esta en el rango valido y que es deterministico.
    """
    dv = _digito_verificacion_nit(base)
    assert 0 <= dv <= 10, f"un DV fuera de 0..10 es imposible: {dv}"
    assert dv == _digito_verificacion_nit(base), "debe ser deterministico"


@pytest.mark.parametrize("base", ["900123456", "800197268", "830001338"])
def test_un_nit_con_su_dv_calculado_se_valida_como_correcto(ctx, base):
    """Ciclo completo: se calcula el DV y se comprueba que la herramienta lo acepta."""
    dv = _digito_verificacion_nit(base)
    r = invocar(ctx, "validar_documento", {"numero": f"{base}{dv}", "tipo": "NIT"})
    assert r.ok is True
    assert r.datos["digito_verificacion_coincide"] is True


@pytest.mark.parametrize("base", ["900123456", "800197268"])
def test_detecta_un_nit_con_digito_de_verificacion_equivocado(ctx, base):
    dv_malo = (_digito_verificacion_nit(base) + 1) % 10
    r = invocar(ctx, "validar_documento", {"numero": f"{base}{dv_malo}", "tipo": "NIT"})
    assert r.ok is True
    assert r.datos["digito_verificacion_coincide"] is False
    assert any("no cuadra" in o for o in r.datos["observaciones"])


def test_detecta_documentos_de_relleno(ctx):
    """'11111111' aparece de verdad en los datos y no identifica a nadie."""
    r = invocar(ctx, "validar_documento", {"numero": "11111111"})
    assert r.ok is True
    assert any("digitos iguales" in o for o in r.datos["observaciones"])


def test_acepta_documento_con_separadores_de_miles(ctx):
    r = invocar(ctx, "validar_documento", {"numero": "41.234.567"})
    assert r.ok is True
    assert r.datos["numero"] == "41234567"


def test_rechaza_algo_que_no_es_un_documento(ctx):
    r = invocar(ctx, "validar_documento", {"numero": "no-soy-un-numero"})
    assert r.ok is False
    assert "no es un numero de documento" in r.error


@pytest.mark.parametrize("numero,tipo", [
    ("AB123456", "PA"),
    ("AB123456", ""),
    ("X1234567A", "PA"),
])
def test_un_pasaporte_se_valida_entero_con_sus_letras(ctx, numero, tipo):
    """
    El prompt manda reportar el pasaporte entero y validarlo con esta
    herramienta. Rechazarlo por llevar letras le diria al agente que un
    documento valido no lo es, y descartaria el campo.
    """
    r = invocar(ctx, "validar_documento", {"numero": numero, "tipo": tipo})

    assert r.ok is True, r.error
    assert r.datos["numero"] == numero
    assert r.datos["alfanumerico"] is True
    # No se mide en digitos: la tabla de longitudes no le aplica.
    assert r.datos["longitud_valida_para_el_tipo"] is None


def test_un_radicado_con_letras_sigue_sin_ser_un_documento(ctx):
    """La excepcion es del pasaporte, no de cualquier cosa con letras."""
    r = invocar(ctx, "validar_documento", {"numero": "ENT3413618689913"})

    assert r.ok is False
    assert "no es un numero de documento" in r.error


# ── Consulta de persona ──

def test_sin_servicio_configurado_lo_dice_en_lugar_de_devolver_vacio(ctx):
    """
    El agente necesita distinguir "no hay servicio" de "la persona no existe":
    son dos conclusiones muy distintas.
    """
    r = invocar(ctx, "consultar_persona", {"numero_documento": "41234567"})
    assert r.ok is False
    assert "no esta disponible" in r.error


def test_consulta_exitosa(ctx):
    ctx.consultar_persona = lambda doc: {"nombre": "Juan Perez", "documento": doc}
    r = invocar(ctx, "consultar_persona", {"numero_documento": "41234567"})
    assert r.ok is True
    assert r.datos["encontrado"] is True
    assert r.datos["nombre"] == "Juan Perez"


def test_persona_no_encontrada_es_exito_con_encontrado_falso(ctx):
    ctx.consultar_persona = lambda doc: None
    r = invocar(ctx, "consultar_persona", {"numero_documento": "99999999"})
    assert r.ok is True
    assert r.datos["encontrado"] is False


def test_si_el_servicio_revienta_no_tumba_al_agente(ctx):
    def explota(_doc):
        raise ConnectionError("sin red")
    ctx.consultar_persona = explota
    r = invocar(ctx, "consultar_persona", {"numero_documento": "41234567"})
    assert r.ok is False
    assert "la consulta fallo" in r.error


# ── Reporte de hallazgos: la evidencia es obligatoria ──

def test_reportar_un_hallazgo_con_evidencia(ctx):
    r = invocar(ctx, "reportar_hallazgo", {
        "campo": "numero_documento_afectado",
        "valor": "41234567",
        "evidencia": "correo linea 1: 'identificado con CC 41234567'",
        "confianza": 0.95,
    })
    assert r.ok is True
    assert len(ctx.hallazgos) == 1
    assert ctx.hallazgos[0]["evidencia"]


def test_un_hallazgo_sin_evidencia_se_rechaza(ctx):
    """La evidencia es obligatoria: sin ella el hallazgo no se registra."""
    r = invocar(ctx, "reportar_hallazgo", {
        "campo": "nombre_afectado", "valor": "Juan Perez",
        "evidencia": "", "confianza": 0.9,
    })
    assert r.ok is False
    assert "obligatoria" in r.error
    assert ctx.hallazgos == []


def test_una_evidencia_demasiado_corta_no_cuenta(ctx):
    r = invocar(ctx, "reportar_hallazgo", {
        "campo": "nombre_afectado", "valor": "Juan", "evidencia": "ahi",
        "confianza": 0.9,
    })
    assert r.ok is False


def test_un_campo_desconocido_se_rechaza_y_lista_los_validos(ctx):
    r = invocar(ctx, "reportar_hallazgo", {
        "campo": "campo_inventado", "valor": "x",
        "evidencia": "una evidencia suficientemente larga", "confianza": 0.5,
    })
    assert r.ok is False
    assert "nombre_afectado" in r.error


@pytest.mark.parametrize("confianza", [-0.1, 1.5, "mucha", None])
def test_la_confianza_se_valida(ctx, confianza):
    r = invocar(ctx, "reportar_hallazgo", {
        "campo": "nombre_afectado", "valor": "Juan Perez",
        "evidencia": "correo linea 2, firma del mensaje",
        "confianza": confianza,
    })
    assert r.ok is False
    assert "confianza" in r.error


def test_todos_los_campos_reportables_se_aceptan(ctx):
    for campo in CAMPOS_REPORTABLES:
        r = invocar(ctx, "reportar_hallazgo", {
            "campo": campo, "valor": 1,
            "evidencia": "evidencia de prueba suficientemente larga",
            "confianza": 0.5,
        })
        assert r.ok is True, f"el campo '{campo}' deberia ser reportable"
