"""
Telemetria: quien resolvio cada campo, cuanto costo, y por que un correo acabo
en revision manual.
"""
from datetime import datetime
from pathlib import Path

import pytest

from harness.atribucion import Origen
from pipeline.telemetria import (
    MOTIVOS_MANUAL,
    _ORIGENES_DEL_INFORME,
    RegistroCorreo,
    leer_serie,
    nueva_ejecucion,
)


MOMENTO = datetime(2026, 8, 24, 13, 30, 4)


@pytest.fixture
def ejecucion(tmp_path):
    return nueva_ejecucion(tmp_path, id_ejecucion="abc123", inicio=MOMENTO)


def _correo(id_correo, accion="radicado", atribucion=None, motivo=""):
    return RegistroCorreo(
        id_correo=id_correo, accion=accion,
        atribucion=atribucion or {}, motivo_manual=motivo,
    )


# ── Conteos y porcentajes ──

def test_cuenta_correos_por_accion(ejecucion):
    for i in range(13):
        ejecucion.registrar(_correo(f"c{i}", "radicado"))
    for i in range(2):
        ejecucion.registrar(_correo(f"m{i}", "manual"))
    ejecucion.registrar(_correo("r1", "reenviado"))

    acciones = ejecucion.por_accion()
    assert acciones["radicado"] == 13
    assert acciones["manual"] == 2
    assert acciones["reenviado"] == 1
    assert ejecucion.total == 16


def test_el_informe_trae_cantidades_y_porcentajes(ejecucion):
    """Sin porcentajes no se puede comparar una corrida de 18 con una de 200."""
    for i in range(9):
        ejecucion.registrar(_correo(f"c{i}", "radicado"))
    for i in range(1):
        ejecucion.registrar(_correo(f"m{i}", "manual"))
    texto = ejecucion.informe()
    assert "CORREOS            10" in texto
    assert "90.0%" in texto  # 9 de 10
    assert "10.0%" in texto  # 1 de 10


# ── Atribucion: la pregunta central ──

def test_atribuye_cada_campo_a_quien_lo_resolvio(ejecucion):
    ejecucion.registrar(_correo("c1", atribucion={
        "numero_documento_afectado": Origen.ALGORITMO,
        "nombre_remitente": Origen.ALGORITMO,
        "tipo_solicitud": Origen.IA,
        "nombre_afectado": Origen.NO_RESUELTO,
    }))
    atrib = ejecucion.atribucion_por_campo()
    assert atrib["numero_documento_afectado"][Origen.ALGORITMO] == 1
    assert atrib["tipo_solicitud"][Origen.IA] == 1
    assert atrib["nombre_afectado"][Origen.NO_RESUELTO] == 1


def test_los_totales_de_atribucion_suman(ejecucion):
    for i in range(4):
        ejecucion.registrar(_correo(f"c{i}", atribucion={
            "a": Origen.ALGORITMO, "b": Origen.IA, "c": Origen.NO_RESUELTO,
        }))
    tot = ejecucion.totales_atribucion()
    assert tot[Origen.ALGORITMO] == 4
    assert tot[Origen.IA] == 4
    assert tot[Origen.NO_RESUELTO] == 4


def test_el_informe_muestra_la_atribucion_por_campo(ejecucion):
    ejecucion.registrar(_correo("c1", atribucion={
        "nombre_remitente": Origen.ALGORITMO, "tipo_solicitud": Origen.IA,
    }))
    texto = ejecucion.informe()
    assert "QUIEN RESOLVIO CADA CAMPO" in texto
    assert "nombre_remitente" in texto
    assert "TOTAL CAMPOS" in texto


def test_el_resumen_cuenta_todas_las_acciones(ejecucion):
    """
    Una accion que no aparezca en el resumen no se puede consultar en el
    explorador de logs, y las cuentas no cuadran con el total de correos.
    """
    for accion in ("radicado", "manual", "duplicado", "reenviado",
                   "aplazado", "fallido"):
        ejecucion.registrar(_correo(f"c-{accion}", accion))

    resumen = ejecucion.resumen()
    contadas = sum(resumen[c] for c in ("radicados", "manual", "duplicados",
                                        "reenviados", "aplazados", "fallidos"))
    assert contadas == resumen["correos"] == 6


def test_el_informe_tiene_una_columna_por_cada_origen():
    """
    Un origen sin columna no da error: desaparece del informe y los
    porcentajes dejan de sumar cien sin que nadie lo note.
    """
    assert set(_ORIGENES_DEL_INFORME) == set(Origen)


def test_los_porcentajes_de_atribucion_suman_cien(ejecucion):
    ejecucion.registrar(_correo("c1", atribucion={
        "nombre_remitente": Origen.ALGORITMO,
        "tipo_solicitud": Origen.IA,
        "telefono_afectado": Origen.REGISTRO,
        "direccion_afectado": Origen.NO_RESUELTO,
    }))
    fila = [l for l in ejecucion.informe().splitlines()
            if "TOTAL CAMPOS" in l][0]
    assert sum(int(p.rstrip("%")) for p in fila.split() if p.endswith("%")) == 100


def test_lo_que_aporta_el_registro_se_cuenta_aparte(ejecucion):
    """
    Su acierto no depende de leer bien el correo sino de haber extraido bien el
    documento: mezclarlo con el algoritmo o con la IA mide otra cosa.
    """
    ejecucion.registrar(_correo("c1", atribucion={
        "telefono_afectado": Origen.REGISTRO,
    }))
    assert "registro" in ejecucion.informe()
    assert ejecucion.totales_atribucion()[Origen.REGISTRO] == 1


def test_el_informe_empieza_por_los_numeros_de_la_corrida():
    """
    La leyenda de como se lee el informe es igual en las cuarenta y ocho
    corridas del dia y esta en el documento de arquitectura. Delante de los
    numeros son diecisiete renglones que hay que saltar en cada una.
    """
    e = nueva_ejecucion(Path("."), id_ejecucion="x", inicio=MOMENTO)
    e.registrar(_correo("c1", atribucion={"a": Origen.ALGORITMO}))
    texto = e.informe()

    assert texto.splitlines()[0].startswith("EJECUCION")
    assert "CORREOS" in texto.splitlines()[2]
    assert "COMO LEER ESTE INFORME" not in texto
    assert "COMO INTERPRETAR LAS CIFRAS" not in texto
    # Los niveles se siguen nombrando: son las columnas de la atribucion.
    for nivel in ("algoritmo", "IA", "manual"):
        assert nivel in texto


@pytest.mark.parametrize("accion,motivo,esperado,no_esperado", [
    ("radicado", "", "radicado ENT1", "revision manual"),
    ("manual", "DOCUMENTO_AMBIGUO", "a revision manual por DOCUMENTO_AMBIGUO", None),
    ("manual", "", "sin motivo registrado", None),
    ("duplicado", "", "", "revision manual"),
    ("reenviado", "", "", "revision manual"),
    ("sin_reclamar", "", "", "revision manual"),
    ("aplazado", "SGDEA_CAIDO", "motivo SGDEA_CAIDO", "revision manual"),
])
def test_el_detalle_describe_el_desenlace_que_tuvo(
        accion, motivo, esperado, no_esperado):
    """
    Un desenlace que no es radicado tampoco es siempre revision manual: un
    duplicado o un reenvio salian descritos como "a revision manual por sin
    motivo", que no es lo que les paso.
    """
    e = nueva_ejecucion(Path("."), id_ejecucion="x", inicio=MOMENTO)
    c = _correo("c1", accion, motivo=motivo, atribucion={"a": Origen.ALGORITMO})
    c.radicado = "ENT1" if accion == "radicado" else ""
    e.registrar(c)
    texto = e.informe()

    if esperado:
        assert esperado in texto
    if no_esperado:
        assert no_esperado not in texto


def test_el_informe_detalla_cada_correo():
    e = nueva_ejecucion(Path("."), id_ejecucion="x", inicio=MOMENTO)
    c = _correo("correo-99", "manual", motivo="DOCUMENTO_AMBIGUO",
                atribucion={"numero_documento_afectado": Origen.NO_RESUELTO})
    c.asunto = "Solicitud de incapacidad"
    e.registrar(c)
    texto = e.informe()
    assert "DETALLE POR CORREO" in texto
    assert "correo-99" in texto
    assert "DOCUMENTO_AMBIGUO" in texto
    assert "Solicitud de incapacidad" in texto
    assert "sin resolver: numero_documento_afectado" in texto


# ── Motivos de revision manual: separar causas ──

def test_agrupa_los_motivos_de_revision_manual(ejecucion):
    ejecucion.registrar(_correo("m1", "manual", motivo="SIN_NOMBRE_REMITENTE"))
    ejecucion.registrar(_correo("m2", "manual", motivo="SIN_NOMBRE_REMITENTE"))
    ejecucion.registrar(_correo("m3", "manual", motivo="CLOUDFLARE_403"))
    motivos = ejecucion.motivos_manual()
    assert motivos["SIN_NOMBRE_REMITENTE"] == 2
    assert motivos["CLOUDFLARE_403"] == 1


def test_separa_los_fallos_de_infraestructura_de_los_de_la_ia(ejecucion):
    """Un fallo de red no mide el desempeño de la IA: van contados aparte."""
    ejecucion.registrar(_correo("m1", "manual", motivo="SIN_NOMBRE_AFECTADO"))
    ejecucion.registrar(_correo("m2", "manual", motivo="CLOUDFLARE_403"))
    ejecucion.registrar(_correo("m3", "manual", motivo="SGDEA_CAIDO"))
    texto = ejecucion.informe()
    assert "2 son por infraestructura" in texto


def test_cada_motivo_del_catalogo_tiene_descripcion():
    for motivo, descripcion in MOTIVOS_MANUAL.items():
        assert descripcion, f"el motivo {motivo} no tiene descripcion"
        assert motivo.isupper(), "los motivos van en mayusculas para el log"


def test_un_motivo_no_catalogado_no_revienta(ejecucion):
    ejecucion.registrar(_correo("m1", "manual", motivo="ALGO_NUEVO"))
    assert "motivo no catalogado" in ejecucion.informe()


# ── Costo ──

def test_el_informe_incluye_el_costo(ejecucion):
    ejecucion.registrar(_correo("c1"))
    ejecucion.contador.registrar("extraccion", "gemini-2.5-flash", 1000, 50)
    texto = ejecucion.informe()
    assert "CONSUMO DEL MODELO" in texto
    assert "costo total" in texto


def test_el_costo_por_correo_se_calcula(ejecucion):
    for i in range(4):
        ejecucion.registrar(_correo(f"c{i}"))
    ejecucion.contador.registrar("x", "gemini-2.5-flash", 4000, 200)
    assert ejecucion.contador.costo_por_correo is not None
    assert "costo por correo" in ejecucion.informe()


def test_un_modelo_sin_tarifa_no_inventa_un_numero(ejecucion):
    """Mejor decir 'no calculable' que estimar y que alguien lo cite como dato."""
    ejecucion.registrar(_correo("c1"))
    ejecucion.contador.registrar("x", "modelo-desconocido-9000", 1000, 50)
    assert ejecucion.contador.costo_usd is None
    assert "no calculable" in ejecucion.informe()


# ── Escritura en disco ──

def test_escribe_el_informe_de_la_ejecucion(ejecucion, tmp_path):
    ejecucion.registrar(_correo("c1", atribucion={"a": Origen.ALGORITMO}))
    rutas = ejecucion.escribir()
    esperado = tmp_path / "ejecuciones" / "2026-08-24" / "133004-abc123.txt"
    # Las rutas vuelven como texto: una direccion `gs://` no es un `Path`.
    assert Path(rutas["ejecucion"]) == esperado
    assert esperado.exists()
    assert "EJECUCION" in esperado.read_text(encoding="utf-8")


def test_el_diario_acumula_varias_ejecuciones(tmp_path):
    """Un archivo por dia, con todas las corridas de ese dia."""
    for hora, ident in ((10, "aaa"), (11, "bbb")):
        e = nueva_ejecucion(tmp_path, id_ejecucion=ident,
                            inicio=datetime(2026, 8, 24, hora, 0, 0))
        e.registrar(_correo("c1"))
        e.escribir()
    diario = (tmp_path / "diario" / "2026-08-24.txt").read_text(encoding="utf-8")
    assert diario.count("EJECUCION") == 2
    assert "aaa" in diario and "bbb" in diario


def test_las_ejecuciones_se_separan_por_dia(tmp_path):
    for dia in (24, 25):
        e = nueva_ejecucion(tmp_path, id_ejecucion=f"d{dia}",
                            inicio=datetime(2026, 8, dia, 9, 0, 0))
        e.registrar(_correo("c1"))
        e.escribir()
    assert (tmp_path / "diario" / "2026-08-24.txt").exists()
    assert (tmp_path / "diario" / "2026-08-25.txt").exists()
    assert (tmp_path / "ejecuciones" / "2026-08-24").is_dir()
    assert (tmp_path / "ejecuciones" / "2026-08-25").is_dir()


# ── Serie historica: la prueba de que mejora ──

def test_la_serie_registra_una_fila_por_ejecucion(tmp_path):
    for i in range(3):
        e = nueva_ejecucion(tmp_path, id_ejecucion=f"e{i}",
                            inicio=datetime(2026, 8, 24, 9 + i, 0, 0))
        e.registrar(_correo("c1", atribucion={"a": Origen.ALGORITMO}))
        e.escribir()
    filas = leer_serie(tmp_path)
    assert len(filas) == 3
    assert {f["id"] for f in filas} == {"e0", "e1", "e2"}


def test_la_serie_permite_ver_la_tendencia(tmp_path):
    """
    La unica prueba de que el sistema mejora: que suba el porcentaje resuelto
    por algoritmo y baje el de revision manual.
    """
    # Corrida vieja: todo lo resuelve la IA
    vieja = nueva_ejecucion(tmp_path, id_ejecucion="vieja",
                            inicio=datetime(2026, 8, 1, 9, 0, 0))
    vieja.registrar(_correo("c1", atribucion={"a": Origen.IA, "b": Origen.IA}))
    vieja.escribir()
    # Corrida nueva: el algoritmo resuelve la mitad
    nueva = nueva_ejecucion(tmp_path, id_ejecucion="nueva",
                            inicio=datetime(2026, 8, 24, 9, 0, 0))
    nueva.registrar(_correo("c1", atribucion={"a": Origen.ALGORITMO, "b": Origen.IA}))
    nueva.escribir()

    filas = leer_serie(tmp_path)
    por_id = {f["id"]: f for f in filas}
    assert int(por_id["vieja"]["pct_algoritmo"]) == 0
    assert int(por_id["nueva"]["pct_algoritmo"]) == 50


def test_la_serie_no_lleva_cabecera_escrita(tmp_path):
    """
    La cabecera la conoce quien lee. Decidirla al escribir obligaba a leer el
    destino primero, y en el bucket leer necesita permiso de listar, que la
    corrida no tiene por que necesitar para escribir su fila.
    """
    for i in range(3):
        e = nueva_ejecucion(tmp_path, id_ejecucion=f"e{i}",
                            inicio=datetime(2026, 8, 24, 9 + i, 0, 0))
        e.registrar(_correo("c1"))
        e.escribir()
    contenido = (tmp_path / "serie.tsv").read_text(encoding="utf-8")

    assert "fecha_hora" not in contenido
    assert len(contenido.strip().splitlines()) == 3
    # Y se sigue leyendo con los nombres de columna correctos.
    filas = leer_serie(tmp_path)
    assert [f["id"] for f in filas] == ["e0", "e1", "e2"]
    assert all("pct_algoritmo" in f for f in filas)


def test_leer_una_serie_inexistente_devuelve_vacio(tmp_path):
    assert leer_serie(tmp_path) == []


# ── Avisos ──

def test_los_avisos_llegan_al_informe(ejecucion):
    c = _correo("c1")
    c.avisos.append("el adjunto 'soporte.pdf' venia protegido con contraseña")
    ejecucion.registrar(c)
    assert "protegido con contraseña" in ejecucion.informe()


def test_una_ejecucion_sin_correos_no_revienta(ejecucion):
    texto = ejecucion.informe()
    assert "CORREOS            0" in texto
    ejecucion.escribir()


def test_el_informe_dice_por_que_no_pudo_con_cada_correo(tmp_path):
    """
    No sale ningun correo explicando el fallo, asi que el informe es donde
    queda: la etiqueta solo lleva el codigo, igual para todos.
    """
    from datetime import datetime

    from pipeline.telemetria import RegistroCorreo, nueva_ejecucion

    frase = ("No se identifica el documento del afectado; los adjuntos son una "
             "historia clinica sin numero de cedula.")
    e = nueva_ejecucion(tmp_path, id_ejecucion="x",
                        inicio=datetime(2026, 8, 27, 14, 0, 0))
    e.registrar(RegistroCorreo(
        id_correo="c1", asunto="Solicitud", accion="manual",
        motivo_manual="SIN_DOCUMENTO", explicacion=frase))

    informe = e.informe()
    assert "por que:" in informe
    assert frase[:60] in informe


def test_un_correo_sin_explicacion_no_deja_la_linea_vacia(tmp_path):
    from datetime import datetime

    from pipeline.telemetria import RegistroCorreo, nueva_ejecucion

    e = nueva_ejecucion(tmp_path, id_ejecucion="x",
                        inicio=datetime(2026, 8, 27, 14, 0, 0))
    e.registrar(RegistroCorreo(id_correo="c1", accion="radicado",
                               radicado="ENT1"))
    assert "por que:" not in e.informe()


# ── Un aviso no puede partir el informe ──

_HTML_DE_CLOUDFLARE = (
    "Radicacion fallida: 403 - <!DOCTYPE HTML>\n"
    "<!--[if lt IE 7]> <html class=\"no-js ie6 oldie\" lang=\"en-US\"> <![endif]-->\n"
    "<!--[if IE 7]>    <html class=\"no-js ie7 oldie\" lang=\"en-US\"> <![endif]-->\n"
    "<head><title>Attention Required! | Cloudflare</title></head>\n"
)


def test_un_aviso_con_saltos_de_linea_no_parte_el_informe():
    """
    El cuerpo de la pagina de desafio de Cloudflare son varias lineas de HTML.
    Con sus saltos dentro, el bloque de avisos dejaba de leerse como un informe:
    cada linea del HTML aparecia como si fuera un renglon del reporte.
    """
    e = nueva_ejecucion(Path("."), id_ejecucion="x", inicio=MOMENTO)
    c = _correo("c1", "aplazado", motivo="CLOUDFLARE_403",
                atribucion={"a": Origen.ALGORITMO})
    c.avisos = [_HTML_DE_CLOUDFLARE]
    e.registrar(c)
    texto = e.informe()

    bloque = texto[texto.index("AVISOS"):].splitlines()
    # La cabecera, la linea de la clase y la del ejemplo. Ni una mas: antes el
    # HTML aportaba un renglon por cada linea que traia.
    assert len(bloque) == 3, bloque
    # La clase agrupa por la causa, sin arrastrar el cuerpo de la respuesta.
    assert bloque[1].strip() == "1x  Radicacion fallida"
    # Y el ejemplo, aunque traiga el HTML, cabe en su renglon.
    assert "DOCTYPE" in bloque[2]


def test_un_asunto_con_saltos_de_linea_tampoco():
    e = nueva_ejecucion(Path("."), id_ejecucion="x", inicio=MOMENTO)
    c = _correo("c1", "radicado", atribucion={"a": Origen.ALGORITMO})
    c.asunto = "Solicitud\nde\ncertificado"
    c.explicacion = "Falta\nla\ndireccion."
    e.registrar(c)
    texto = e.informe()

    for ln in texto.splitlines():
        if "asunto:" in ln:
            assert ln.strip() == "asunto: Solicitud de certificado"
        if "por que:" in ln:
            assert ln.strip() == "por que: Falta la direccion."


def test_el_fallo_registrado_cabe_en_un_renglon():
    """`linea_log` es la representacion de un renglon: tiene que serlo siempre."""
    from pipeline.errores import Bitacora

    b = Bitacora()
    b.registrar("RADICACION", _HTML_DE_CLOUDFLARE,
                cuerpo=_HTML_DE_CLOUDFLARE)

    for linea in b.avisos():
        assert "\n" not in linea


# ── La frase del agente solo explica el correo que se cerro por ella ──

def test_un_correo_radicado_no_publica_la_frase_del_agente():
    """
    La escribe el agente al cerrar y dice que dato NO encontro. En un correo
    radicado ese dato no impidio nada, y la frase se escribe antes de que el
    registro rellene y antes de la compuerta: puede nombrar un campo que para
    cuando se radico ya estaba resuelto.
    """
    from pipeline.telemetria import explica_el_desenlace

    assert not explica_el_desenlace("radicado", "")
    assert not explica_el_desenlace("duplicado", "")
    assert not explica_el_desenlace("reenviado", "")
    assert not explica_el_desenlace("aplazado", "SGDEA_CAIDO")


def test_el_correo_cerrado_por_el_contenido_si_la_publica():
    from pipeline.telemetria import explica_el_desenlace

    assert explica_el_desenlace("manual", "SIN_DOCUMENTO")
    assert explica_el_desenlace("manual", "DOCUMENTO_AMBIGUO")


def test_ni_siquiera_a_manual_si_la_causa_fue_de_infraestructura():
    from pipeline.telemetria import explica_el_desenlace

    assert not explica_el_desenlace("manual", "SGDEA_CAIDO")
    assert not explica_el_desenlace("manual", "CLOUDFLARE_403")


def test_el_detalle_del_informe_calla_en_un_radicado():
    from pipeline.telemetria import RegistroCorreo

    e = nueva_ejecucion(Path("."), id_ejecucion="x", inicio=MOMENTO)
    e.registrar(RegistroCorreo(
        id_correo="AgaCiMGAAA", asunto="Sustitucion pensional",
        accion="radicado", radicado="ENT20260001742948",
        explicacion="No se identifica la direccion del afectado."))

    informe = e.informe()

    assert "ENT20260001742948" in informe
    assert "No se identifica la direccion" not in informe
