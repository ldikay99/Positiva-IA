"""
Recuperacion dentro del workspace: busqueda normalizada, indice por hoja,
lectura por pagina y consulta BM25.

Encontrar un dato no puede depender de las tildes, las mayusculas, los saltos
de linea que deja el OCR ni los separadores de miles.
"""
import json

import pytest

from pipeline import indice
from pipeline.workspace import ESTADO_OK, EntradaManifiesto, Workspace
from servidor_mcp.herramientas import HERRAMIENTAS, Contexto, invocar

ADJUNTO = """# soportes.pdf

## Pagina 1

DERECHO DE PETICION
Señores Positiva ARL, respetuosamente solicito el reconocimiento de mis
incapacidades. Ley 1755 de 2015.

## Pagina 2 (OCR, confianza 0.58)

REPUBLICA DE COLOMBIA
IDENTIFICACION PERSONAL
CÉDULA DE CIUDADANÍA
NUMERO 1.087.995.336
PEREZ GOMEZ
FECHA Y LUGAR DE NACIMIENTO

## Pagina 3

HISTORIA CLÍNICA - EPICRISIS
Motivo de consulta: dolor lumbar tras caida en el trabajo.
Enfermedad actual: paciente femenina de 43 años.
Diagnóstico CIE10 M545

## Pagina 4

CERTIFICADO DE INCAPACIDAD
Dias de incapacidad: 15. Fecha inicio 01/10/2026 fecha fin 15/10/2026.
Paciente identificada con
cédula 1087995336
"""


@pytest.fixture
def ws(tmp_path):
    w = Workspace.crear("indice", base=tmp_path, temporal=False)
    w.escribir_correo("Buen dia, envio la Gestión del caso.\nAtte Ana")
    ruta = w.escribir_adjunto(1, "soportes.pdf", ADJUNTO)
    w.manifiesto.agregar(EntradaManifiesto(
        nombre_original="soportes.pdf",
        ruta_md=str(ruta.relative_to(w.raiz)),
        estado=ESTADO_OK, caracteres_md=len(ADJUNTO), ocr_aplicado=True))
    yield w
    w.cerrar()


@pytest.fixture
def ctx(ws):
    return Contexto(workspace=ws)


# ── Plegado ──

def test_plegar_conserva_la_longitud():
    for t in ("Cédula NIÑO Ünico", "ﬁ ligadura", "İstanbul", "abc"):
        assert len(indice.plegar(t)) == len(t)
    assert indice.plegar("CÉDULA Ñandú") == "cedula nandu"


def test_plegar_patron_no_toca_los_escapes():
    assert indice.plegar_patron(r"CÉDULA\D\S") == r"cedula\D\S"


# ── Busqueda normalizada ──

def test_buscar_sin_tilde_encuentra_con_tilde(ws):
    h = ws.buscar("cedula de ciudadania")
    assert h and h[0]["pagina"] == 2


def test_buscar_con_tilde_y_mayusculas_encuentra_sin_tilde(ws):
    assert ws.buscar("GESTION")[0]["documento"] == "correo"
    assert ws.buscar("gestión")[0]["documento"] == "correo"


def test_buscar_encuentra_dato_partido_en_dos_lineas(ws):
    h = ws.buscar("identificada con cedula")
    assert len(h) == 1
    assert h[0]["pagina"] == 4
    assert "identificada con" in h[0]["coincidencia"]


def test_buscar_cifra_admite_separadores_de_miles(ws):
    paginas = sorted(h["pagina"] for h in ws.buscar("1087995336"))
    assert paginas == [2, 4]


def test_buscar_cifra_con_puntos_encuentra_la_cifra_sin_puntos(ws):
    paginas = sorted(h["pagina"] for h in ws.buscar("1.087.995.336"))
    assert paginas == [2, 4]


def test_buscar_correo_sin_paginas_no_trae_pagina(ws):
    h = ws.buscar("Atte")
    assert h and "pagina" not in h[0]


def test_buscar_linea_larga_acota_el_contexto(tmp_path):
    with Workspace.crear("larga", base=tmp_path, temporal=False) as w:
        w.escribir_correo("x " * 3000 + "CC 41234567 " + "y " * 3000)
        h = w.buscar("41234567")
        assert len(h) == 1
        assert "41234567" in h[0]["coincidencia"]
        assert "41234567" in h[0]["contexto"]
        assert len(h[0]["contexto"]) < 1000


def test_la_herramienta_sigue_rechazando_patrones_anidados(ctx):
    r = invocar(ctx, "buscar_en_documentos", {"patron": "(a+)+$"})
    assert not r.ok and "colgar el proceso" in r.error


def test_la_herramienta_sanea_y_trae_la_pagina(ctx):
    r = invocar(ctx, "buscar_en_documentos", {"patron": "epicrisis"})
    assert r.ok
    c = r.datos["coincidencias"][0]
    assert c["pagina"] == 3
    assert "INICIO_DEL_DOCUMENTO" in c["contexto"]


# ── Indice por hoja ──

def test_clasificador_de_hojas():
    assert indice.clasificar_hoja("ACCION DE TUTELA\nJuzgado Segundo") == "tutela"
    assert indice.clasificar_hoja("FURAT formato unico de reporte de accidente") == "furat"
    assert indice.clasificar_hoja("Dictamen de perdida de capacidad laboral") == "dictamen"
    assert indice.clasificar_hoja("Planilla integrada PILA aportante IBC") == "planilla"
    assert indice.clasificar_hoja("FACTURA ELECTRONICA DE VENTA CUFE") == "factura"
    assert indice.clasificar_hoja("hola") == "otro"
    assert indice.clasificar_hoja("_(pagina sin texto extraible)_") == "ilegible"


def test_inventario_lleva_indice_por_hoja(ws):
    adjunto = next(d for d in ws.listar() if d["tipo"] == "adjunto")
    assert adjunto["paginas"] == [
        "p1 peticion",
        "p2 cedula ocr0.58 id1:CC",
        "p3 historia_clinica",
        "p4 incapacidad id1:CC",
    ]


def test_encabezado_de_pagina_con_cualquier_sufijo():
    lineas = ["## Pagina 1 (OCR)", "a", "## Pagina 2 (OCR, confianza 0,7)",
              "b", "## Pagina 3", "c"]
    p = indice.paginas_de(lineas)
    assert [(x.numero, x.ocr, x.confianza) for x in p] == [
        (1, True, None), (2, True, 0.7), (3, False, None)]


def test_documento_largo_agrupa_rangos():
    lineas = []
    for i in range(1, 21):
        lineas += [f"## Pagina {i}", "Historia clinica. Motivo de consulta: control."]
    assert indice.describir_paginas(lineas) == ["p1-20 historia_clinica"]


def test_por_tipo_resume_un_adjunto_variado():
    lineas = []
    textos = ["Historia clinica. Motivo de consulta.", "FACTURA ELECTRONICA CUFE",
              "Historia clinica. Motivo de consulta. CC 41234567"]
    for i in range(1, 13):
        lineas += [f"## Pagina {i}", textos[i % 3]]
    r = indice.describir_paginas(lineas, nivel=indice.NIVEL_POR_TIPO)
    assert r == ["factura p1,4,7,10", "historia_clinica p2-3,5-6,8-9,11-12",
                 "id p2,5,8,11:CC"]


def test_hoja_con_basura_de_control_es_ilegible():
    assert indice.clasificar_hoja("\x00\x01\x02\x03 \x04\x05 historia") == "ilegible"


def test_inventario_respeta_el_presupuesto_del_indice(tmp_path):
    from pipeline.workspace import PRESUPUESTO_INDICE

    with Workspace.crear("muchos", base=tmp_path, temporal=False) as w:
        textos = ["Historia clinica. Motivo de consulta. CC 41234567",
                  "FACTURA ELECTRONICA CUFE", "ACCION DE TUTELA juzgado"]
        for k in range(6):
            lineas = []
            for i in range(1, 31):
                lineas += [f"## Pagina {i}", textos[(i + k) % 3]]
            ruta = w.escribir_adjunto(k, f"a{k}.pdf", "\n".join(lineas))
            w.manifiesto.agregar(EntradaManifiesto(
                nombre_original=f"a{k}.pdf", ruta_md=str(ruta.relative_to(w.raiz)),
                estado=ESTADO_OK))
        inv = w.listar()
        total = sum(len(json.dumps(d["paginas"], ensure_ascii=False))
                    for d in inv if "paginas" in d)
        assert total <= PRESUPUESTO_INDICE


def test_adjunto_sin_paginas_lleva_su_clase(tmp_path):
    with Workspace.crear("sinpag", base=tmp_path, temporal=False) as w:
        ruta = w.escribir_adjunto(1, "t.docx", "ACCION DE TUTELA\nJuzgado 3")
        w.manifiesto.agregar(EntradaManifiesto(
            nombre_original="t.docx", ruta_md=str(ruta.relative_to(w.raiz)),
            estado=ESTADO_OK))
        adj = [d for d in w.listar() if d["tipo"] == "adjunto"][0]
        assert adj["clase"] == "tutela"


def test_inventario_es_compacto(ctx):
    r = invocar(ctx, "listar_documentos")
    assert len(json.dumps(r.a_dict(), ensure_ascii=False)) < 800


# ── Leer por pagina ──

def test_leer_documento_por_pagina(ctx, ws):
    adjunto = next(d["id"] for d in ws.listar() if d["tipo"] == "adjunto")
    r = invocar(ctx, "leer_documento", {"documento": adjunto, "pagina": 3})
    assert r.ok
    assert r.datos["pagina"] == 3
    assert "EPICRISIS" in r.datos["contenido"]
    assert "IDENTIFICACION PERSONAL" not in r.datos["contenido"]
    assert "CERTIFICADO DE INCAPACIDAD" not in r.datos["contenido"]


def test_leer_pagina_inexistente_dice_cuales_hay(ctx, ws):
    adjunto = next(d["id"] for d in ws.listar() if d["tipo"] == "adjunto")
    r = invocar(ctx, "leer_documento", {"documento": adjunto, "pagina": 9})
    assert not r.ok and "1 a 4" in r.error


def test_leer_pagina_de_documento_sin_paginas(ctx):
    r = invocar(ctx, "leer_documento", {"documento": "correo", "pagina": 1})
    assert not r.ok and "lineas" in r.error


# ── Consulta BM25 ──

def test_consulta_trae_la_hoja_pertinente(ctx):
    r = invocar(ctx, "consultar_documentos",
                {"pregunta": "días de incapacidad"})
    assert r.ok
    primero = r.datos["tramos"][0]
    assert primero["pagina"] == 4
    assert "Dias de incapacidad" in primero["texto"]
    assert "INICIO_DEL_DOCUMENTO" in primero["texto"]


def test_consulta_cifra_con_y_sin_puntos(ws):
    paginas = {t.get("pagina") for t in ws.consultar("cedula 1087995336", maximo=5)}
    assert {2, 4} <= paginas


def test_consulta_sin_coincidencias(ctx):
    r = invocar(ctx, "consultar_documentos", {"pregunta": "helicoptero"})
    assert r.ok and r.datos["tramos"] == []


def test_consulta_cabe_en_la_respuesta_del_agente(ctx):
    r = invocar(ctx, "consultar_documentos",
                {"pregunta": "cedula paciente incapacidad", "maximo": 5})
    assert len(json.dumps(r.a_dict(), ensure_ascii=False)) < 2_500 + 600


def test_consulta_registrada_y_declarada():
    from pipeline.agente import ESQUEMAS_HERRAMIENTAS, SISTEMA

    assert "consultar_documentos" in HERRAMIENTAS
    assert "consultar_documentos" in {e["name"] for e in ESQUEMAS_HERRAMIENTAS}
    assert "consultar_documentos" in SISTEMA


def test_bm25_prefiere_el_tramo_con_el_termino_raro():
    b = indice.BM25([["paciente", "dolor"], ["paciente", "cedula"], ["paciente"]])
    q = ["cedula", "paciente"]
    assert b.puntaje(1, q) > b.puntaje(0, q)
