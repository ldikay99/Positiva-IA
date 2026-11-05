"""
Pruebas del workspace y de la materializacion a Markdown.

Lo que se valida no es que el codigo corra, sino que se cumplan las reglas que
hacen auditable el pipeline: nada se pierde en silencio, el tipo se decide por
los bytes, los duplicados no se convierten dos veces, y la limpieza esta
garantizada aunque el procesamiento reviente.
"""
import io
import json
import zipfile

import pytest

from pipeline.conversion import (
    LIMITE_CARACTERES,
    convertir,
    detectar_tipo,
    expandir_zip,
    normalizar,
)
from pipeline.materializar import (
    cuerpo_a_markdown,
    es_ruido,
    hilo_a_markdown,
    materializar,
    materializar_adjuntos,
)
from pipeline.workspace import (
    ESTADO_DEMASIADO_GRANDE,
    Workspace,
    nombre_de_directorio,
)


# ── Ciclo de vida ──

def test_el_workspace_se_limpia_al_salir_del_contexto():
    with Workspace.crear("correo-1") as ws:
        raiz = ws.raiz
        ws.escribir_correo("# hola\n\ncontenido\n")
        assert raiz.exists()
    assert not raiz.exists(), "el workspace temporal debe borrarse"


def test_se_limpia_incluso_si_revienta_el_procesamiento():
    """La limpieza no puede depender de que todo salga bien."""
    raiz = None
    with pytest.raises(RuntimeError):
        with Workspace.crear("correo-2") as ws:
            raiz = ws.raiz
            ws.escribir_correo("algo")
            raise RuntimeError("fallo simulado en el procesamiento")
    assert raiz is not None and not raiz.exists()


def test_el_manifiesto_se_escribe_aunque_falle(tmp_path):
    """El manifiesto es el registro de lo que se intento: siempre se guarda."""
    raiz = None
    with pytest.raises(RuntimeError):
        with Workspace.crear("correo-3", base=tmp_path, temporal=False) as ws:
            raiz = ws.raiz
            ws.manifiesto.avisar("algo salio mal")
            raise RuntimeError("boom")
    manifiesto = json.loads(
        (raiz / "manifest.json").read_text(encoding="utf-8"))
    assert "algo salio mal" in manifiesto["avisos"]


def test_no_temporal_se_conserva_para_inspeccion(tmp_path):
    with Workspace.crear("correo-4", base=tmp_path, temporal=False) as ws:
        raiz = ws.raiz
    assert raiz.exists()


def test_el_id_con_caracteres_raros_no_rompe_la_ruta(tmp_path):
    """Los IDs de Graph traen '=' y '/', que no sirven como nombre de carpeta."""
    with Workspace.crear("AAMkAD/cyMDJ=mMDA3+LTVh", base=tmp_path,
                         temporal=False) as ws:
        assert ws.raiz.exists()
        ws.escribir_correo("x")


# ── Deteccion de tipo por bytes, no por extension ──

@pytest.mark.parametrize("datos,esperado", [
    (b"%PDF-1.7\n...", "pdf"),
    (b"\x89PNG\r\n\x1a\n" + b"\x00" * 20, "imagen"),
    (b"\xff\xd8\xff\xe0" + b"\x00" * 20, "imagen"),
    (b"Solicito una copia del certificado", "texto"),
    (b"", "vacio"),
])
def test_detecta_el_tipo_por_contenido(datos, esperado):
    assert detectar_tipo(datos, "cualquiera.bin") == esperado


def test_la_extension_mentirosa_no_engana():
    """Un PDF llamado .doc o una imagen llamada .pdf deben ir al conversor real."""
    assert detectar_tipo(b"%PDF-1.4 contenido", "documento.doc") == "pdf"
    assert detectar_tipo(b"\x89PNG\r\n\x1a\n\x00\x00", "escaneo.pdf") == "imagen"


def test_distingue_docx_de_xlsx_de_zip():
    """Los tres son ZIP por dentro: hay que mirar la estructura interna."""
    def zip_con(nombres):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for n in nombres:
                z.writestr(n, "x")
        return buf.getvalue()

    assert detectar_tipo(zip_con(["word/document.xml"]), "a.zip") == "docx"
    assert detectar_tipo(zip_con(["xl/workbook.xml"]), "a.zip") == "xlsx"
    assert detectar_tipo(zip_con(["carpeta/archivo.txt"]), "a.zip") == "zip"


# ── Normalizacion ──

def test_la_normalizacion_conserva_los_saltos_que_son_estructura():
    texto = "Titulo\n\nParrafo uno\nParrafo dos"
    assert normalizar(texto).count("\n") >= 3


@pytest.mark.parametrize("invisible,nombre", [
    ("​", "espacio de ancho cero"),
    ("‌", "no-joiner"),
    ("‎", "marca izquierda-a-derecha"),
    ("‮", "override derecha-a-izquierda (invierte el texto en pantalla)"),
    ("‭", "override izquierda-a-derecha"),
    ("⁦", "aislante bidi"),
    ("⁠", "word joiner"),
    ("﻿", "BOM incrustado"),
])
def test_elimina_caracteres_invisibles(invisible, nombre):
    """
    Los invisibles son el vehiculo habitual del texto oculto en documentos.
    El caso mas peligroso es U+202E: invierte el texto EN PANTALLA, asi que el
    documento muestra una cosa y contiene otra. Un revisor humano no lo ve.
    """
    limpio = normalizar(f"Maria{invisible}Gomez")
    assert invisible not in limpio, f"no se elimino: {nombre}"
    assert limpio == "MariaGomez"


def test_elimina_caracteres_de_control():
    assert "\x00" not in normalizar("antes\x00despues")
    assert "\x1b" not in normalizar("antes\x1bdespues")


def test_normaliza_espacios_duros_y_saltos_windows():
    limpio = normalizar("a b\r\nc")
    assert " " not in limpio
    assert "\r" not in limpio


def test_texto_nulo_no_revienta():
    assert normalizar(None) == ""
    assert normalizar("") == ""


# ── Conversion ──

def test_convierte_texto_plano_con_encabezado():
    conv = convertir("Solicito certificado laboral".encode("utf-8"), "peticion.txt")
    assert conv.error == ""
    assert conv.markdown.startswith("# peticion.txt")
    assert "Solicito certificado laboral" in conv.markdown


def test_el_encabezado_permite_atribuir_el_dato_a_su_archivo():
    """Sin saber de que documento salio un dato no hay evidencia posible."""
    conv = convertir(b"CC 41234567", "cedula-escaneada.txt")
    assert "cedula-escaneada.txt" in conv.markdown


def test_un_tipo_no_soportado_no_revienta_y_explica_por_que():
    conv = convertir(b"\xd0\xcf\x11\xe0" + b"\x00" * 40, "viejo.doc")
    assert conv.error != ""
    assert "no soportado" in conv.error


def test_archivo_vacio_se_reporta_como_tal():
    conv = convertir(b"", "vacio.pdf")
    assert "vacio" in conv.error


def test_el_contenido_muy_largo_se_trunca_y_se_avisa():
    grande = ("linea de relleno\n" * 40_000).encode("utf-8")
    conv = convertir(grande, "enorme.txt")
    assert conv.truncado is True
    assert len(conv.markdown) < LIMITE_CARACTERES + 2_000


# ── PDF: cifrado, paginas ilegibles y atribucion por pagina ──

def _pdf_de_prueba(texto="Solicito el certificado. CC 41234567", clave=None):
    """Genera un PDF real, opcionalmente cifrado, para las pruebas."""
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    pagina = doc.new_page()
    pagina.insert_text((72, 72), texto)
    if clave is None:
        datos = doc.tobytes()
    else:
        # Solo contraseña de PROPIETARIO: restringe imprimir, no abrir.
        datos = doc.tobytes(
            encryption=pymupdf.PDF_ENCRYPT_AES_256,
            owner_pw=clave, user_pw="",
            permissions=pymupdf.PDF_PERM_ACCESSIBILITY,
        )
    doc.close()
    return datos


def test_convierte_un_pdf_nativo_sin_ocr():
    conv = convertir(_pdf_de_prueba(), "peticion.pdf")
    assert conv.error == ""
    assert "41234567" in conv.markdown
    assert conv.ocr_aplicado is False, "un PDF con texto no debe pasar por OCR"


def test_convierte_un_pdf_con_solo_contrasena_de_propietario():
    """
    Una contraseña de propietario restringe imprimir, no abrir: hay que
    autenticar con cadena vacia o se pierde el adjunto completo.
    """
    conv = convertir(_pdf_de_prueba(clave="propietario"), "DOC-WA0000.pdf")
    assert conv.error == "", f"deberia poder leerse: {conv.error}"
    assert "41234567" in conv.markdown


def test_recupera_un_pdf_protegido_con_el_documento_del_correo():
    """
    El documento que el ciudadano escribe en el cuerpo es la clave con la que
    protege su adjunto: probarla recupera el soporte en vez de perderlo
    entero.
    """
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Incapacidad del trabajador")
    protegido = doc.tobytes(
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        owner_pw="dueno", user_pw="41234567",
    )
    doc.close()

    # Sin la clave no se puede leer
    sin_clave = convertir(protegido, "soporte.pdf")
    assert "contraseña" in sin_clave.error

    # Con el documento hallado en el correo, si
    con_clave = convertir(protegido, "soporte.pdf",
                          claves_candidatas=["80111222", "41234567"])
    assert con_clave.error == ""
    assert "Incapacidad del trabajador" in con_clave.markdown
    assert "clave candidata" in con_clave.aviso


def test_extrae_claves_candidatas_del_texto():
    from pipeline.materializar import claves_candidatas_de
    claves = claves_candidatas_de(
        "Adjunto incapacidad, clave del PDF: 41234567. Tel 3105551234"
    )
    assert "41234567" in claves
    assert len(claves) <= 12


def test_las_claves_candidatas_no_se_vuelven_fuerza_bruta():
    from pipeline.materializar import claves_candidatas_de
    muchos = " ".join(str(1000000 + i) for i in range(200))
    assert len(claves_candidatas_de(muchos)) <= 12


def test_una_pagina_corrupta_no_cuesta_el_documento_entero(monkeypatch):
    """Cada pagina se convierte por separado: si una falla, el resto se lee."""
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    for i in range(3):
        doc.new_page().insert_text((72, 72), f"contenido pagina {i + 1}")
    datos = doc.tobytes()
    doc.close()

    original = pymupdf.Document.load_page
    def load_page_con_fallo(self, i):
        if i == 1:
            raise RuntimeError("pagina corrupta simulada")
        return original(self, i)
    monkeypatch.setattr(pymupdf.Document, "load_page", load_page_con_fallo)

    conv = convertir(datos, "parcial.pdf")
    assert conv.error == ""
    assert "contenido pagina 1" in conv.markdown
    assert "contenido pagina 3" in conv.markdown
    assert "no se pudo leer" in conv.markdown
    assert "no se pudieron leer" in conv.aviso


def test_el_pdf_emite_encabezado_por_pagina():
    """La atribucion por pagina permite citar de donde salio cada dato."""
    conv = convertir(_pdf_de_prueba(), "documento.pdf")
    assert "## Pagina 1" in conv.markdown


def test_sin_motor_de_ocr_la_imagen_degrada_con_motivo(monkeypatch):
    """
    En este entorno no hay binario de tesseract; en produccion el Dockerfile
    lo instala. Lo que importa es que la ausencia quede REGISTRADA con su
    motivo y no se pierda en silencio.
    """
    png = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    conv = convertir(png, "cedula.jpg")
    assert conv.tipo_real == "imagen"
    # O convierte, o explica por que no pudo: nunca silencio
    assert conv.error or conv.markdown


# ── ZIP: contenido que viene de fuera y hay que tratar con desconfianza ──

def test_expande_un_zip_normal():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("uno.txt", "contenido uno")
        z.writestr("dos.txt", "contenido dos")
    internos = expandir_zip(buf.getvalue())
    assert {n for n, _ in internos} == {"uno.txt", "dos.txt"}


def test_rechaza_rutas_que_escapan_del_zip():
    """
    Zip slip: una entrada con '..' escribiria fuera del destino. El contenido
    viene de un correo externo, asi que se rechaza.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("../../etc/passwd", "malicioso")
        z.writestr("bueno.txt", "ok")
    internos = expandir_zip(buf.getvalue())
    nombres = [n for n, _ in internos]
    assert "bueno.txt" in nombres
    assert not any("passwd" in n for n in nombres)


def test_limita_la_cantidad_de_entradas_del_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for i in range(100):
            z.writestr(f"archivo-{i}.txt", "x")
    assert len(expandir_zip(buf.getvalue(), limite=10)) == 10


def test_un_zip_corrupto_no_revienta():
    assert expandir_zip(b"PK\x03\x04corrupto") == []


# ── Filtro de ruido ──

@pytest.mark.parametrize("nombre", [
    "image001.png", "outlook-firma.jpg", "logo_empresa.png", "signature.gif",
])
def test_reconoce_adjuntos_decorativos(nombre):
    assert es_ruido(nombre) is True


@pytest.mark.parametrize("nombre", [
    "incapacidad.pdf", "cedula.jpg", "historia_clinica.pdf", "soporte.xlsx",
])
def test_no_descarta_adjuntos_con_informacion(nombre):
    assert es_ruido(nombre) is False


# ── Materializacion ──

def test_materializa_un_correo_completo(tmp_path):
    ws = materializar(
        id_correo="correo-10",
        asunto="Solicitud de incapacidad",
        cuerpo_limpio="Buen dia, adjunto la incapacidad.",
        remitente_nombre="Maria Gomez",
        remitente_correo="maria@empresa.com",
        hilo=[("Pedro Ruiz", "2026-08-01", "Mensaje anterior del hilo")],
        adjuntos=[("soporte.txt", b"CC 41234567 del trabajador")],
        base=str(tmp_path), temporal=False,
    )
    try:
        assert (ws.raiz / "correo.md").exists()
        assert (ws.raiz / "hilo.md").exists()
        assert len(list((ws.raiz / "adjuntos").glob("*.md"))) == 1
        assert len(ws.manifiesto.convertidos) == 1
    finally:
        ws.cerrar()


def test_el_hilo_atribuye_cada_mensaje_a_su_remitente():
    """Sin atribucion la firma de un mensaje anterior se confunde con la del actual."""
    md = hilo_a_markdown([
        ("Ana Torres", "2026-08-01", "primer mensaje"),
        ("Beto Gil", "2026-08-02", "segundo mensaje"),
    ])
    assert "Ana Torres" in md and "Beto Gil" in md
    assert md.count("## Mensaje") == 2


def test_el_hilo_vacio_no_genera_archivo():
    assert hilo_a_markdown([]) == ""


def test_el_cuerpo_usa_el_asunto_como_titulo():
    md = cuerpo_a_markdown("Solicitud PQRD", "contenido del correo")
    assert md.startswith("# Solicitud PQRD")


def test_sin_asunto_no_deja_titulo_vacio():
    assert "(sin asunto)" in cuerpo_a_markdown("", "cuerpo")


def test_no_convierte_dos_veces_el_mismo_contenido(tmp_path):
    """
    Los hilos reenvian el mismo adjunto en cada mensaje. Convertirlo cada vez
    es gasto puro de OCR.
    """
    mismo = b"contenido identico del soporte"
    with Workspace.crear("correo-11", base=tmp_path, temporal=False) as ws:
        convertidos = materializar_adjuntos(ws, [
            ("soporte.txt", mismo),
            ("soporte-copia.txt", mismo),
        ])
        assert convertidos == 1
        duplicado = [e for e in ws.manifiesto.entradas
                     if "duplicado" in (e.detalle or "")]
        assert len(duplicado) == 1


def test_registra_el_motivo_cuando_un_adjunto_es_demasiado_grande(tmp_path):
    from pipeline import materializar as mod
    original = mod.LIMITE_BYTES_ADJUNTO
    mod.LIMITE_BYTES_ADJUNTO = 100
    try:
        with Workspace.crear("correo-12", base=tmp_path, temporal=False) as ws:
            materializar_adjuntos(ws, [("grande.txt", b"x" * 500)])
            entrada = ws.manifiesto.entradas[0]
            assert entrada.estado == ESTADO_DEMASIADO_GRANDE
            assert "excede" in entrada.detalle
            assert ws.manifiesto.avisos, "un adjunto omitido debe dejar aviso"
    finally:
        mod.LIMITE_BYTES_ADJUNTO = original


def test_ningun_fallo_de_conversion_queda_en_silencio(tmp_path):
    """Si un adjunto no se pudo procesar, queda escrito en el manifiesto."""
    with Workspace.crear("correo-13", base=tmp_path, temporal=False) as ws:
        materializar_adjuntos(ws, [
            ("bueno.txt", b"contenido valido"),
            ("raro.doc", b"\xd0\xcf\x11\xe0" + b"\x00" * 40),
        ])
        assert len(ws.manifiesto.convertidos) == 1
        assert len(ws.manifiesto.fallidos) == 1
        assert ws.manifiesto.avisos, "el fallo debe dejar aviso explicito"
        assert ws.manifiesto.fallidos[0].detalle


def test_expande_zip_y_convierte_su_contenido(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("interno.txt", "documento dentro del zip")
    with Workspace.crear("correo-14", base=tmp_path, temporal=False) as ws:
        convertidos = materializar_adjuntos(ws, [("paquete.zip", buf.getvalue())])
        assert convertidos == 1
        assert any("interno" in e.nombre_original for e in ws.manifiesto.convertidos)


# ── Lectura: lo que consumiran las herramientas MCP ──

def test_listar_no_devuelve_contenido_solo_inventario(tmp_path):
    """Listar debe ser barato: la IA decide que leer sin pagar por leerlo."""
    ws = materializar(
        "correo-15", "Asunto", "cuerpo del correo",
        adjuntos=[("a.txt", b"contenido a")],
        base=str(tmp_path), temporal=False,
    )
    try:
        inventario = ws.listar()
        assert any(d["id"] == "correo" for d in inventario)
        for doc in inventario:
            assert "caracteres" in doc
            assert "contenido" not in doc
    finally:
        ws.cerrar()


def test_leer_por_rango_de_lineas(tmp_path):
    with Workspace.crear("correo-16", base=tmp_path, temporal=False) as ws:
        ws.escribir_correo("\n".join(f"linea {i}" for i in range(20)))
        trozo = ws.leer("correo", desde=5, hasta=8)
        assert trozo.splitlines() == ["linea 5", "linea 6", "linea 7"]


def test_leer_un_documento_inexistente_falla_claro(tmp_path):
    with Workspace.crear("correo-17", base=tmp_path, temporal=False) as ws:
        with pytest.raises(FileNotFoundError):
            ws.leer("no-existe")


def test_buscar_devuelve_ubicacion_y_contexto(tmp_path):
    with Workspace.crear("correo-18", base=tmp_path, temporal=False) as ws:
        ws.escribir_correo(
            "primera linea\nCC 41234567 del trabajador\ntercera linea"
        )
        hallazgos = ws.buscar(r"\d{8}")
        assert len(hallazgos) == 1
        assert hallazgos[0]["documento"] == "correo"
        assert hallazgos[0]["linea"] == 1
        assert "primera linea" in hallazgos[0]["contexto"]


def test_buscar_con_patron_invalido_falla_claro(tmp_path):
    with Workspace.crear("correo-19", base=tmp_path, temporal=False) as ws:
        ws.escribir_correo("x")
        with pytest.raises(ValueError):
            ws.buscar("[sin cerrar")


# ── Cada correo trabaja en su propio directorio ──

_ID_BASE = (
    "AAMkADcyMDJmMDA3LTVhNzItNDVlNS04NWViLWVjZmUyNWJiYTcxMQBGAAAAAAANrHHR"
    "d1pDTamvbofpSEoqBwBLcuGHFAssTaRrL8tDDEqfAAckGdkXAABLcuGHFAssTaRrL8tDDEqf"
)


def test_dos_correos_del_mismo_buzon_no_comparten_directorio():
    """
    Los identificadores de Graph del mismo buzon coinciden en casi todo su
    largo y se diferencian al final. Recortarlos daba la misma carpeta para
    todos los correos de la bandeja, y cada uno pisaba los documentos del
    anterior.
    """
    uno = nombre_de_directorio(_ID_BASE + "AAgT0zHOAAA=")
    otro = nombre_de_directorio(_ID_BASE + "AAgT0zFcAAA=")

    assert uno != otro


def test_el_mismo_correo_siempre_da_el_mismo_directorio():
    """Reprocesarlo tiene que caer en su sitio, no crear uno nuevo."""
    id_correo = _ID_BASE + "AAgT0zHOAAA="
    assert nombre_de_directorio(id_correo) == nombre_de_directorio(id_correo)


def test_un_identificador_vacio_no_produce_un_nombre_vacio():
    assert nombre_de_directorio("").strip("-._")


def test_los_documentos_de_un_correo_no_se_ven_desde_otro(tmp_path):
    """Es el efecto que importa: el agente lee lo que hay en SU workspace."""
    uno = Workspace.crear(_ID_BASE + "AAgT0zHOAAA=", base=tmp_path, temporal=False)
    uno.escribir_correo("cuerpo del correo UNO")
    otro = Workspace.crear(_ID_BASE + "AAgT0zFcAAA=", base=tmp_path, temporal=False)
    otro.escribir_correo("cuerpo del correo OTRO")

    assert uno.raiz != otro.raiz
    assert uno.leer("correo") == "cuerpo del correo UNO"
    assert otro.leer("correo") == "cuerpo del correo OTRO"


# ── Imagenes dentro de una pagina que ya tiene texto ──

def _pdf_con_texto_y_una_imagen(fraccion_de_la_hoja):
    """
    Una pagina con texto propio y una imagen que ocupa la fraccion pedida.

    La imagen lleva un numero, para poder comprobar si su contenido llego al
    markdown o se quedo fuera.
    """
    pymupdf = pytest.importorskip("pymupdf")

    sello = pymupdf.open()
    hoja = sello.new_page(width=400, height=250)
    hoja.insert_textbox(pymupdf.Rect(20, 20, 380, 230),
                        "NUMERO 41234567", fontsize=20, fontname="helv")
    png = hoja.get_pixmap(dpi=150).tobytes("png")
    sello.close()

    doc = pymupdf.open()
    pagina = doc.new_page(width=595, height=842)
    pagina.insert_textbox(
        pymupdf.Rect(50, 40, 545, 200),
        "Solicito la reclasificacion de mi afiliacion. Adjunto mi documento.",
        fontsize=11, fontname="helv")
    area = 595 * 842 * fraccion_de_la_hoja
    lado_x = (area * 1.6) ** 0.5
    lado_y = area / lado_x
    pagina.insert_image(
        pymupdf.Rect(20, 220, 20 + lado_x, 220 + lado_y), stream=png)
    datos = doc.tobytes()
    doc.close()
    return datos


def test_la_imagen_dentro_de_una_pagina_con_texto_si_se_lee():
    """
    El ciudadano escribe su solicitud y pega la foto de la cedula debajo. La
    pagina TIENE texto, asi que sin esto no se aplica OCR y el documento que
    venia en la imagen no se lee nunca.
    """
    pytest.importorskip("pytesseract")
    conv = convertir(_pdf_con_texto_y_una_imagen(0.25), "solicitud.pdf")

    assert conv.error == ""
    assert "reclasificacion" in conv.markdown
    if "Imagen incrustada" not in conv.markdown:
        pytest.skip("sin binario de tesseract en este entorno")
    assert "41234567" in conv.markdown


def test_un_escaneo_de_pagina_completa_no_se_lee_dos_veces():
    """
    Cuando la imagen ES la pagina, el escaner ya dejo su capa de texto y esa
    capa esta completa: pasarla otra vez por OCR duplica el texto con mas ruido
    y mete los numeros del membrete, que el extractor tomaria por documentos.
    """
    conv = convertir(_pdf_con_texto_y_una_imagen(0.95), "escaneo.pdf")

    assert conv.error == ""
    assert "Imagen incrustada" not in conv.markdown


def test_una_imagen_pequena_no_se_pasa_por_ocr():
    """Un logotipo o una firma no llevan datos y el OCR cuesta tiempo."""
    conv = convertir(_pdf_con_texto_y_una_imagen(0.02), "con-logo.pdf")

    assert conv.error == ""
    assert "Imagen incrustada" not in conv.markdown


def test_tesseract_no_abre_un_hilo_por_nucleo_de_la_maquina():
    """
    Tesseract mira los nucleos de la maquina, no la cuota del contenedor. Con
    dos vCPU y cinco correos en vuelo, cinco tesseract con una decena de hilos
    cada uno se pelean por dos nucleos y cada pagina tarda varias veces mas.

    No cambia la concurrencia del pipeline: son los hilos internos de cada
    llamada.
    """
    import os

    # `convertir` ya viene importado arriba: importar el modulo es lo que lo
    # fija, antes de la primera llamada al OCR.
    assert convertir is not None
    assert os.environ.get("OMP_THREAD_LIMIT") == "1"


# ── El presupuesto de OCR es del correo, no de cada documento ──

def test_el_presupuesto_de_ocr_se_reparte_entre_los_adjuntos(tmp_path):
    """
    El tope por documento no acota el correo: dieciseis escaneos multiplican
    noventa segundos por dieciseis y la corrida se pasa de la ventana.
    """
    from pipeline import conversion

    original = conversion.PRESUPUESTO_OCR_POR_CORREO_S
    conversion.PRESUPUESTO_OCR_POR_CORREO_S = 0.0
    try:
        with Workspace.crear("correo-p1", base=tmp_path, temporal=False) as ws:
            materializar_adjuntos(ws, [
                ("escaneo-uno.pdf", _pdf_escaneado()),
                ("escaneo-dos.pdf", _pdf_escaneado("Otro contenido")),
            ])
            avisos = " ".join(ws.manifiesto.avisos)
            assert "presupuesto de OCR del correo" in avisos
            assert "sin convertir" in avisos
    finally:
        conversion.PRESUPUESTO_OCR_POR_CORREO_S = original


def test_lo_que_queda_sin_convertir_no_pasa_por_convertido(tmp_path):
    """
    Devolverlo como vacio manda el correo a revision manual por "sin
    documento", que es el motivo equivocado: el documento venia.
    """
    from pipeline import conversion
    from pipeline.workspace import ESTADO_ERROR

    original = conversion.PRESUPUESTO_OCR_POR_CORREO_S
    conversion.PRESUPUESTO_OCR_POR_CORREO_S = 0.0
    try:
        with Workspace.crear("correo-p2", base=tmp_path, temporal=False) as ws:
            convertidos = materializar_adjuntos(
                ws, [("escaneo.pdf", _pdf_escaneado())])
            assert convertidos == 0
            entrada = ws.manifiesto.entradas[0]
            assert entrada.estado == ESTADO_ERROR
            assert "presupuesto de OCR del correo" in entrada.detalle
    finally:
        conversion.PRESUPUESTO_OCR_POR_CORREO_S = original


def test_el_presupuesto_del_correo_acota_al_del_documento():
    """
    Si al correo le quedan diez segundos, el documento no puede gastar noventa.
    """
    from pipeline.conversion import (
        PRESUPUESTO_OCR_SEGUNDOS,
        PresupuestoDeOcr,
    )

    p = PresupuestoDeOcr(total_s=100.0)
    assert p.queda() == 100.0
    p.gastar(95.0)
    assert p.queda() == 5.0
    assert min(PRESUPUESTO_OCR_SEGUNDOS, p.queda()) == 5.0
    p.gastar(10.0)
    assert p.agotado is True
    assert p.queda() == 0.0


def test_un_correo_normal_no_se_queda_sin_presupuesto(tmp_path):
    """El tope no puede recortar trabajo normal: tres PDF nativos caben."""
    with Workspace.crear("correo-p3", base=tmp_path, temporal=False) as ws:
        convertidos = materializar_adjuntos(ws, [
            ("uno.pdf", _pdf_de_prueba("Primer documento")),
            ("dos.pdf", _pdf_de_prueba("Segundo documento")),
            ("tres.pdf", _pdf_de_prueba("Tercer documento")),
        ])
        assert convertidos == 3
        assert not any("presupuesto de OCR del correo" in a
                       for a in ws.manifiesto.avisos)


def _pdf_escaneado(texto="Solicito el certificado. CC 41234567"):
    """
    Un PDF SIN capa de texto, como el que sale de un escaner.

    El de `_pdf_de_prueba` trae texto nativo y no gasta OCR: no sirve para
    probar el presupuesto.
    """
    pymupdf = pytest.importorskip("pymupdf")
    origen = pymupdf.open()
    pagina = origen.new_page()
    pagina.insert_text((72, 72), texto)
    png = pagina.get_pixmap(dpi=150).tobytes("png")
    origen.close()

    doc = pymupdf.open()
    hoja = doc.new_page()
    hoja.insert_image(hoja.rect, stream=png)
    datos = doc.tobytes()
    doc.close()
    return datos
