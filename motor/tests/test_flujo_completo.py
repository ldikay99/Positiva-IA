"""
Flujo completo sobre el corpus real: materializar, sanear, extraer, decidir.

Si el corpus no esta recolectado las pruebas se omiten: no se versiona.
"""
from pathlib import Path

import pytest

from harness.caso import cargar_corpus, extraer_de
from pipeline.errores import Bitacora
from pipeline.extraccion import extraer_todo
from pipeline.materializar import materializar
from pipeline.saneamiento import Severidad, detectar
from pipeline.telemetria import RegistroCorreo, nueva_ejecucion
from harness.atribucion import Origen
from utils.generic_filter import es_nombre_persona_valido

RAIZ_CORPUS = Path(__file__).resolve().parent.parent / "corpus" / "casos"


@pytest.fixture(scope="module")
def corpus():
    casos = cargar_corpus(RAIZ_CORPUS)
    if not casos:
        pytest.skip("corpus no recolectado; correr harness.recolectar")
    return casos


@pytest.fixture(scope="module")
def extraidos(corpus):
    """
    La extraccion de cada caso, una sola vez por modulo, por el mismo punto
    de entrada que la cobertura y la compuerta de regresion.
    """
    return [(caso, extraer_de(caso)) for caso in corpus]


def test_ningun_nombre_extraido_lleva_vocabulario_administrativo(extraidos):
    """
    Ningun nombre extraido del corpus contiene una palabra de
    `_VOCABULARIO_NO_NOMBRE`.

    Comprueba la propiedad sobre todos los casos, sin fijar ningun nombre
    concreto del corpus.
    """
    from pipeline.extraccion import _VOCABULARIO_NO_NOMBRE, _normaliza

    contaminados = []
    for caso, e in extraidos:
        nombre = e.valor("nombre_afectado")
        if not nombre:
            continue
        sobrantes = [
            p for p in nombre.split()
            if _normaliza(p).strip(".,;:") in _VOCABULARIO_NO_NOMBRE
        ]
        if sobrantes:
            contaminados.append((caso.id, sobrantes))
    assert not contaminados, (
        f"nombres con vocabulario administrativo dentro: {contaminados[:5]}")


def test_todo_nombre_extraido_tiene_al_menos_dos_palabras(extraidos):
    """Un apellido suelto no identifica a nadie y no debe radicarse."""
    cortos = []
    for caso, e in extraidos:
        nombre = e.valor("nombre_afectado")
        if nombre and len(nombre.split()) < 2:
            cortos.append((caso.id, nombre))
    assert not cortos, f"nombres de una sola palabra: {cortos[:5]}"


# ── Un correo nunca puede quedar como nombre del remitente ──

def test_ningun_correo_del_corpus_produce_un_correo_como_nombre(extraidos):
    """`nombre_remitente` nunca puede quedar con una direccion de correo."""
    fallos = []
    for caso, e in extraidos:
        nombre = e.valor("nombre_remitente")
        if nombre and not es_nombre_persona_valido(nombre):
            fallos.append((caso.id, nombre))
    assert not fallos, f"se produjo un nombre invalido en: {fallos[:5]}"


def test_ningun_campo_resuelto_llega_sin_evidencia(extraidos):
    """Sin procedencia no hay auditoria posible."""
    for caso, e in extraidos:
        for campo, h in e.hallazgos.items():
            assert h.evidencia, f"{caso.id}: {campo} sin evidencia"
            assert h.confianza > 0, f"{caso.id}: {campo} con confianza cero"


# ── El harness tiene que medir lo mismo que produccion ──

@pytest.mark.parametrize("markdown,ilegible", [
    ("# CEDULA.pdf\n\n## Pagina 1\n\n_(pagina sin texto extraible)_\n", True),
    ("# CEDULA.pdf\n\n## Pagina 1\n\n_(pagina sin texto extraible)_\n"
     "## Pagina 2\n\n_(pagina sin texto extraible)_\n", True),
    ("# CEDULA.pdf\n\n## Pagina 1\n\nCC 41234567 PEDRO RUIZ\n", False),
    # Basta que UNA pagina de texto, igual que en la conversion.
    ("# HC.pdf\n\n## Pagina 1\n\n_(pagina sin texto extraible)_\n"
     "## Pagina 2\n\nDiagnostico: fractura\n", False),
])
def test_se_reconoce_el_adjunto_del_que_no_se_pudo_leer_nada(markdown, ilegible):
    """
    En produccion los marca el conversor y llegan a `extraer_todo`, que
    distingue "no hay documento" de "el documento esta en un escaneo ilegible".
    El corpus guarda el markdown ya convertido, asi que la condicion se
    reconstruye de su contenido.
    """
    from harness.caso import Caso, adjuntos_ilegibles_de

    caso = Caso(id="c", asunto="a", remitente_nombre="n",
                remitente_correo="n@e.com", adjuntos={"anexo": markdown})
    assert bool(adjuntos_ilegibles_de(caso)) is ilegible


def test_un_adjunto_ilegible_cambia_el_motivo_del_documento():
    """
    Un adjunto ilegible da su propio motivo: el operador tiene que saber que
    el dato esta en un escaneo que no se pudo leer.
    """
    from harness.caso import Caso, extraer_de

    caso = Caso(
        id="c", asunto="Envio de documentos",
        remitente_nombre="Ana Lopez", remitente_correo="ana@empresa.com",
        cuerpo_md="Adjunto la cedula del trabajador.",
        adjuntos={"cedula": "# CEDULA.pdf\n\n## Pagina 1\n\n"
                            "_(pagina sin texto extraible)_\n"},
    )
    e = extraer_de(caso)
    nr = e.no_resueltos["numero_documento_afectado"]
    assert nr.motivo == "DOCUMENTO_EN_ADJUNTO_ILEGIBLE"
    assert "cedula" in nr.detalle


def test_todo_campo_no_resuelto_lleva_un_motivo_accionable(extraidos):
    """El motivo debe ser un codigo, no prosa libre."""
    for caso, e in extraidos:
        for campo, nr in e.no_resueltos.items():
            assert nr.motivo, f"{caso.id}: {campo} sin motivo"
            assert nr.motivo.isupper(), (
                f"{caso.id}: el motivo '{nr.motivo}' deberia ser un codigo"
            )
            assert nr.detalle, f"{caso.id}: {campo} sin detalle"


# ── Propiedades del conjunto ──

def test_la_extraccion_nunca_revienta_sobre_datos_reales(corpus, extraidos):
    """
    Los correos reales traen de todo: cuerpos vacios, OCR sucio, hilos
    anidados. Ninguno debe producir una excepcion no controlada: si alguno la
    produjera, el fixture reventaria antes de llegar aqui.
    """
    assert len(extraidos) == len(corpus)


def test_la_clasificacion_semantica_siempre_se_escala(extraidos):
    """
    `tipo_solicitud` solo se resuelve cuando el texto NOMBRA el tramite. Si no
    lo nombra, elegir entre 27 categorias exige leer la intencion y se escala.

    `tipo_remitente` si se resuelve, pero solo puede concluir "Afectado":
    separar familiar de tercero exige leer la relacion. Se comprueba el VALOR y
    no una lista de metodos permitidos, que habria que ampliar a mano cada vez
    que aparece una forma nueva de fijar la identidad.
    """
    from pipeline.extraccion import TIPO_REMITENTE_AFECTADO

    for caso, e in extraidos:
        h_sol = e.hallazgos.get("tipo_solicitud")
        if h_sol is None:
            assert "tipo_solicitud" in e.no_resueltos
        else:
            assert h_sol.metodo == "tramite_nombrado_en_el_texto", (
                f"{caso.id}: el algoritmo concluyo {h_sol.valor} por {h_sol.metodo}")
        h = e.hallazgos.get("tipo_remitente")
        if h is None:
            assert "tipo_remitente" in e.no_resueltos
            continue
        assert int(h.valor) == TIPO_REMITENTE_AFECTADO, (
            f"{caso.id}: el algoritmo concluyo {h.valor} por {h.metodo}")


def test_el_resumen_para_la_ia_no_repite_lo_ya_resuelto(extraidos):
    """El resumen lleva solo lo pendiente: su tamaño no crece con el del correo."""
    for caso, e in extraidos[:20]:
        resumen = e.resumen_para_ia()
        # El resumen es corto por diseño: si creciera con el tamaño del correo,
        # se volveria el problema que se quiere eliminar.
        assert len(resumen) < 4000, "el resumen para la IA crecio demasiado"
        if not e.no_resueltos:
            # Sin nada pendiente no hay a quien decirle que no lo busque: el
            # correo se resuelve entero sin llamar al modelo.
            assert "sin IA" in resumen
        elif e.campos_resueltos:
            assert "no lo vuelvas a buscar" in resumen


def test_el_detector_de_inyeccion_no_marca_correo_real_como_ataque(corpus):
    """
    Cero falsos positivos de severidad alta: marcar correo legitimo cuesta mas
    que el ataque que se pretende evitar.
    """
    marcados = []
    for caso in corpus:
        texto = f"{caso.asunto}\n{caso.cuerpo_md}\n" + "\n".join(caso.adjuntos.values())
        altas = [s for s in detectar(texto) if s.severidad is Severidad.ALTA]
        if altas:
            marcados.append((caso.id, [s.categoria for s in altas]))
    assert not marcados, f"falsos positivos de severidad alta: {marcados[:5]}"


# ── Integracion con el resto del pipeline ──

def test_materializar_y_extraer_encadenan(tmp_path):
    """El workspace produce lo que la extraccion consume, sin adaptadores."""
    ws = materializar(
        id_correo="flujo-1",
        asunto="Incapacidad del trabajador PEDRO RUIZ",
        cuerpo_limpio="Adjunto incapacidad del trabajador PEDRO RUIZ con CC 41234567.\n"
                      "Contacto 310 555 1234.\nCordialmente, Ana Lopez",
        remitente_nombre="Ana Lopez",
        remitente_correo="ana.lopez@empresa.com",
        adjuntos=[("soporte.txt", b"Incapacidad del trabajador PEDRO RUIZ")],
        base=str(tmp_path), temporal=False,
    )
    try:
        e = extraer_todo(
            asunto=ws.manifiesto.asunto,
            cuerpo=ws.leer("correo"),
            texto_adjuntos="\n".join(
                ws.leer(Path(x.ruta_md).stem) for x in ws.manifiesto.convertidos
            ),
            from_name=ws.manifiesto.remitente_nombre,
            from_address=ws.manifiesto.remitente_correo,
        )
        assert e.valor("numero_documento_afectado") == "41234567"
        assert e.valor("nombre_remitente") == "Ana Lopez"
    finally:
        ws.cerrar()


def test_el_flujo_registra_telemetria_con_atribucion(tmp_path):
    """
    Cierra el ciclo: lo que extrajo el algoritmo aparece atribuido en el
    informe, que es lo que permite demostrar la mejora.
    """
    e = extraer_todo(
        asunto="Incapacidad", cuerpo="CC 41234567 del trabajador PEDRO RUIZ",
        from_name="Ana Lopez", from_address="ana@empresa.com",
    )
    ejecucion = nueva_ejecucion(tmp_path, id_ejecucion="flujo")
    registro = RegistroCorreo(id_correo="c1", accion="radicado")
    for campo in e.campos_resueltos:
        registro.atribucion[campo] = Origen.ALGORITMO
    for campo in e.campos_pendientes:
        registro.atribucion[campo] = Origen.IA
    ejecucion.registrar(registro)

    informe = ejecucion.informe()
    assert "QUIEN RESOLVIO CADA CAMPO" in informe
    assert "nombre_remitente" in informe
    rutas = ejecucion.escribir()
    assert Path(rutas["ejecucion"]).exists()


def test_un_fallo_degradado_no_impide_radicar_pero_queda_en_el_informe(tmp_path):
    """Degradar es aceptable; degradar en silencio no: la perdida de una capa queda en el informe."""
    b = Bitacora()
    b.registrar("REDIS", "no se guardo el hash de deduplicacion",
                cuerpo="Error -2 ... Name or service not known")
    assert b.puede_radicar is True

    ejecucion = nueva_ejecucion(tmp_path, id_ejecucion="degradado")
    registro = RegistroCorreo(id_correo="c1", accion="radicado", avisos=b.avisos())
    ejecucion.registrar(registro)
    assert "DNS_NO_RESUELVE" in ejecucion.informe()


def test_un_fallo_fatal_da_motivo_manual_catalogado(tmp_path):
    from pipeline.telemetria import MOTIVOS_MANUAL

    b = Bitacora()
    b.registrar("RADICACION", "captcha al radicar", estado=403,
                cuerpo="<title>Attention Required! | Cloudflare</title>")
    assert b.puede_radicar is False
    motivo = b.motivo_manual()
    assert motivo in MOTIVOS_MANUAL, (
        f"el motivo '{motivo}' no esta en la taxonomia de revision manual"
    )
