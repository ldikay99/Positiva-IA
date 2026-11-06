"""
Todo fallo que degrada el procesamiento queda registrado con su motivo.

Cubre los puntos donde el correo continua aunque una comprobacion no se haya
podido hacer: etiquetado, movimiento, OCR, consultas, expansion de ZIP y la
tabla de oficinas.
"""
import pytest

from models.email import EmailAddress, EmailBody, EmailMessage
from pipeline.compuerta import validar
from pipeline.conversion import Conversion, _ocr_de_pagina
from pipeline.extraccion import Hallazgo
from pipeline.modelo import Llamada, ModeloFalso, Turno
from pipeline.orquestador import Orquestador


def _email(asunto="Pago de incapacidad", cuerpo=(
        "Incapacidad del trabajador PEDRO RUIZ MEJIA con C.C. 41234567. "
        "Cordialmente, Ana Lopez")):
    return EmailMessage(
        id="c1",
        subject=asunto,
        body=EmailBody(content=cuerpo),
        from_address=EmailAddress(name="Ana Lopez", address="ana@empresa.com"),
        conversation_id="hilo-1",
    )


def _modelo():
    return ModeloFalso(turnos=[
        Turno(llamadas=[
            Llamada("reportar_hallazgo", {
                "campo": "tipo_solicitud", "valor": "16",
                "evidencia": "asunto del correo", "confianza": 0.9}),
            Llamada("reportar_hallazgo", {
                "campo": "tipo_remitente", "valor": "2",
                "evidencia": "firma del correo", "confianza": 0.9}),
        ]),
        Turno(texto="LISTO"),
    ])


class RadicadorFalso:
    bitacora = None
    suposiciones = ()

    async def radicar(self, email, hallazgos, archivos=None, cuerpo_limpio=""):
        return "ENT20260001"


# ── Etiquetado y movimiento del correo ──

class OutlookQueFalla:
    """
    Doble del servicio de correo que falla en una operacion concreta.

    `desde_la_llamada` permite dejar pasar las primeras. La primera marca es la
    de la reclamacion, que es bloqueante: para probar que un fallo en las marcas
    de CIERRE no tumba la radicacion hay que dejar que el correo se reclame.
    """

    def __init__(self, falla_en, desde_la_llamada=0):
        self.falla_en = falla_en
        self.desde_la_llamada = desde_la_llamada
        self.hechos = []

    def _toca_fallar(self, operacion):
        if self.falla_en != operacion:
            return False
        return self.hechos.count(operacion) > self.desde_la_llamada

    async def marcar(self, id_correo, marca="", quitar_de_proceso=False):
        self.hechos.append("marcar")
        if self._toca_fallar("marcar"):
            raise PermissionError("403 sin permiso de escritura")
        from pipeline.marcas import fusionar

        return fusionar([], marca, quitar_de_proceso)

    async def etiquetar_correo(self, id_correo, etiqueta):
        self.hechos.append("etiquetar_correo")
        if self.falla_en == "etiquetar_correo":
            raise PermissionError("403 sin permiso de escritura")

    async def move_email(self, id_correo, carpeta):
        self.hechos.append("move_email")
        if self.falla_en == "move_email":
            raise TimeoutError("Graph no responde")

    async def hilo_ya_radicado(self, conversation_id):
        return None


@pytest.mark.asyncio
async def test_si_no_se_pudo_etiquetar_queda_dicha_la_consecuencia(tmp_path):
    """
    El aviso nombra la consecuencia de quedarse sin etiqueta: las capas 1 y 3
    de deduplicacion dependen de ella, asi que el correo puede radicarse dos
    veces.
    """
    outlook = OutlookQueFalla("marcar", desde_la_llamada=1)
    orq = Orquestador(modelo=_modelo(), outlook=outlook,
                      radicador=RadicadorFalso(), base_workspace=str(tmp_path))
    r = await orq.procesar(_email())

    assert r.accion == "radicado", r.motivo_manual
    aviso = next((a for a in r.avisos if "fallo" in a and "radicarse" in a), "")
    assert aviso, r.avisos
    assert "PermissionError" in aviso
    assert "radicarse dos veces" in aviso


@pytest.mark.asyncio
async def test_si_no_se_pudo_mover_el_correo_queda_dicho(tmp_path):
    outlook = OutlookQueFalla("move_email")
    orq = Orquestador(modelo=_modelo(), outlook=outlook,
                      radicador=RadicadorFalso(), base_workspace=str(tmp_path))
    r = await orq.procesar(_email())

    aviso = next((a for a in r.avisos if "move_email fallo" in a), "")
    assert aviso, r.avisos
    assert "bandeja de entrada" in aviso


@pytest.mark.asyncio
async def test_etiquetar_falla_pero_igual_se_intenta_mover(tmp_path):
    """La radicacion ya ocurrio: no se pierde por no poder etiquetar."""
    outlook = OutlookQueFalla("marcar", desde_la_llamada=1)
    orq = Orquestador(modelo=_modelo(), outlook=outlook,
                      radicador=RadicadorFalso(), base_workspace=str(tmp_path))
    await orq.procesar(_email())
    # Dos marcas: la del reclamo, que pasa, y la del radicado, que falla. El
    # movimiento se intenta igual porque la radicacion ya ocurrio.
    assert outlook.hechos == ["marcar", "marcar", "move_email"]


@pytest.mark.asyncio
async def test_un_correo_a_revision_manual_tambien_avisa(tmp_path):
    outlook = OutlookQueFalla("marcar", desde_la_llamada=1)
    orq = Orquestador(modelo=ModeloFalso(turnos=[Turno(texto="LISTO")]),
                      outlook=outlook, radicador=RadicadorFalso(),
                      base_workspace=str(tmp_path))
    # Sin tramite nombrado, `tipo_solicitud` depende del modelo: con el modelo
    # callado, el correo va a revision manual y es ahi donde tiene que avisar.
    r = await orq.procesar(_email(
        asunto="Documentos del trabajador",
        cuerpo="Adjunto los documentos de PEDRO RUIZ MEJIA con C.C. 41234567."))

    assert r.accion == "manual"
    assert any("marcar el correo" in a and "fallo" in a
               for a in r.avisos), r.avisos


@pytest.mark.asyncio
async def test_si_no_se_pudo_reclamar_el_correo_no_se_procesa(tmp_path):
    """
    Reclamar es lo que impide que dos corridas trabajen el mismo correo. Si la
    marca no se escribe el correo queda sin dueño, y seguir permitiria que la
    corrida siguiente lo tomara tambien y las dos lo radicaran.
    """
    outlook = OutlookQueFalla("marcar")
    radicador = RadicadorFalso()
    orq = Orquestador(modelo=_modelo(), outlook=outlook,
                      radicador=radicador, base_workspace=str(tmp_path))
    r = await orq.procesar(_email())

    assert r.accion == "sin_reclamar"
    assert not r.radicado
    # Solo se intento la marca del reclamo: nada mas se toco del correo.
    assert outlook.hechos == ["marcar"]
    assert any("sin dueño" in a for a in r.avisos), r.avisos


@pytest.mark.asyncio
async def test_el_correo_sin_reclamar_no_genera_aviso(tmp_path):
    """
    No se hizo nada con el: un aviso lo presentaria como un desenlace, y la
    corrida siguiente lo va a tomar y avisar lo que corresponda.
    """
    class OutlookQueApunta(OutlookQueFalla):
        def __init__(self):
            super().__init__("marcar")
            self.avisados = []

        async def forward_email(self, id_correo, destino, cuerpo):
            self.avisados.append(destino)

    outlook = OutlookQueApunta()
    orq = Orquestador(modelo=_modelo(), outlook=outlook,
                      radicador=RadicadorFalso(), base_workspace=str(tmp_path))
    r = await orq.procesar(_email())

    assert r.accion == "sin_reclamar"
    assert outlook.avisados == []


# ── OCR ──

class PaginaSinPixmap:
    def get_pixmap(self, dpi=200):
        raise RuntimeError("mupdf: cannot render page")


def test_el_ocr_distingue_falta_de_motor_de_fallo_real():
    """
    Ambas situaciones dan texto vacio, asi que el motivo es lo unico que
    distingue "falta el motor de OCR" de "la pagina no se pudo renderizar".
    """
    texto, motivo = _ocr_de_pagina(PaginaSinPixmap())
    assert texto == ""
    assert motivo
    assert "OCR" in motivo or "motor" in motivo


def test_un_escaneo_que_no_se_pudo_leer_se_marca_ilegible():
    """Todas las paginas sin texto: el motivo manual debe ser ADJUNTO_ILEGIBLE."""
    c = Conversion(markdown="# x\n\n_(pagina sin texto extraible)_",
                   tipo_real="pdf", paginas=3, paginas_ilegibles=3)
    assert c.ilegible


def test_un_pdf_con_una_pagina_mala_no_se_marca_ilegible():
    """Si alguna pagina si dio texto, el documento se leyo."""
    c = Conversion(markdown="# x\n\ncontenido", tipo_real="pdf",
                   paginas=3, paginas_ilegibles=1)
    assert not c.ilegible


def test_una_conversion_sin_paginas_no_se_marca_ilegible():
    """Un docx no tiene paginas: el campo no aplica y no debe activarse."""
    assert not Conversion(markdown="# x\n\ntexto", tipo_real="docx").ilegible


# ── Consultas de persona ──

@pytest.mark.asyncio
async def test_si_sgdea_esta_caido_el_agente_no_oye_que_la_persona_no_existe(tmp_path):
    """
    Un servicio caido se le reporta al agente como error de consulta, no como
    persona inexistente.
    """
    from servidor_mcp.herramientas import Contexto, consultar_persona

    class SgdeaCaido:
        def consultar(self, documento):
            raise ConnectionError("sgdea no responde")

    orq = Orquestador(modelo=_modelo(), sgdea=SgdeaCaido(),
                      base_workspace=str(tmp_path))
    r = orq._consultar_persona
    resultado = consultar_persona(Contexto(workspace=None, consultar_persona=r),
                                  "41234567")
    assert not resultado.ok
    assert "fallo" in resultado.error


@pytest.mark.asyncio
async def test_un_cliente_que_se_traga_el_error_tampoco_engaña_al_agente(tmp_path):
    """
    Un cliente que captura la excepcion y devuelve None deja el motivo en
    `ultimo_error`; el orquestador lo lee para no reportar "no existe".
    """
    from servidor_mcp.herramientas import Contexto, consultar_persona

    class SgdeaQueSeTragaElError:
        ultimo_error = "ConnectionError: el servicio no responde"

        def consultar(self, documento):
            return None

    orq = Orquestador(modelo=_modelo(), sgdea=SgdeaQueSeTragaElError(),
                      base_workspace=str(tmp_path))
    resultado = consultar_persona(
        Contexto(workspace=None, consultar_persona=orq._consultar_persona),
        "41234567")
    assert not resultado.ok


@pytest.mark.parametrize("modulo,clase", [
    ("services.outlook", "OutlookService"),
    ("services.redis_client", "RedisService"),
    ("services.supabase_client", "SupabaseService"),
])
def test_los_clientes_de_consulta_exponen_ultimo_error(modulo, clase):
    """
    Sus consultas devuelven None o False tanto si la respuesta es negativa como
    si el servicio fallo. `ultimo_error` es lo que separa ambos casos.
    """
    import importlib

    servicio = getattr(importlib.import_module(modulo), clase)
    assert hasattr(servicio, "ultimo_error")


@pytest.mark.asyncio
async def test_una_persona_que_de_verdad_no_existe_se_reporta_como_tal(tmp_path):
    from servidor_mcp.herramientas import Contexto, consultar_persona

    class SgdeaSano:
        def consultar(self, documento):
            return None

    orq = Orquestador(modelo=_modelo(), sgdea=SgdeaSano(),
                      base_workspace=str(tmp_path))
    resultado = consultar_persona(
        Contexto(workspace=None, consultar_persona=orq._consultar_persona),
        "41234567")
    assert resultado.ok
    assert resultado.datos["encontrado"] is False


# ── Un ZIP que pierde entradas ──

def _zip(archivos):
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for nombre, contenido in archivos:
            z.writestr(nombre, contenido)
    return buf.getvalue()


def test_un_zip_normal_no_reporta_descartes():
    from pipeline.conversion import expandir_zip

    descartados = []
    salida = expandir_zip(_zip([("cedula.txt", b"41234567")]),
                          descartados=descartados)
    assert len(salida) == 1
    assert descartados == []


def test_una_ruta_que_escapa_del_zip_queda_reportada():
    from pipeline.conversion import expandir_zip

    descartados = []
    salida = expandir_zip(_zip([("../../etc/passwd", b"x"),
                                ("cedula.txt", b"41234567")]),
                          descartados=descartados)
    assert [n for n, _ in salida] == ["cedula.txt"]
    assert any("escapa del comprimido" in d for d in descartados)


def test_un_zip_con_demasiadas_entradas_lo_dice():
    from pipeline.conversion import expandir_zip

    descartados = []
    expandir_zip(_zip([(f"a{i}.txt", b"x") for i in range(40)]),
                 limite=5, descartados=descartados)
    assert any("40 entradas" in d for d in descartados)


def test_un_zip_dañado_lo_dice():
    from pipeline.conversion import expandir_zip

    descartados = []
    salida = expandir_zip(b"PK\x03\x04" + b"\xff" * 200, descartados=descartados)
    assert salida == []
    assert any("dañado" in d for d in descartados)


def test_lo_descartado_de_un_zip_llega_al_manifiesto(tmp_path):
    """Lo descartado al expandir un ZIP aparece en los avisos del manifiesto."""
    from pipeline.materializar import materializar

    ws = materializar(
        id_correo="z1", asunto="Soportes", cuerpo_limpio="adjunto soportes",
        remitente_nombre="Ana Lopez", remitente_correo="ana@empresa.com",
        adjuntos=[("soportes.zip", _zip([("../fuera.txt", b"x"),
                                         ("cedula.txt", b"41234567")]))],
        base=str(tmp_path), temporal=False,
    )
    try:
        assert any("escapa del comprimido" in a for a in ws.manifiesto.avisos), \
            ws.manifiesto.avisos
    finally:
        ws.cerrar()


# ── La compuerta sin su tabla ──

def test_si_falta_la_tabla_de_oficinas_no_se_da_por_verificada(monkeypatch):
    """Sin la tabla, la oficina queda con reparo en lugar de darse por valida."""
    import builtins

    original = builtins.__import__

    def sin_business_rules(nombre, *a, **kw):
        if nombre == "business_rules":
            raise ImportError("no module named business_rules")
        return original(nombre, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", sin_business_rules)
    d = validar({
        "numero_documento_afectado": Hallazgo(
            "numero_documento_afectado", "41234567", "e", 0.9),
        "nombre_afectado": Hallazgo("nombre_afectado", "PEDRO RUIZ", "e", 0.9),
        "nombre_remitente": Hallazgo("nombre_remitente", "Ana Lopez", "e", 0.9),
        "tipo_solicitud": Hallazgo("tipo_solicitud", 16, "e", 0.9),
        "oficina": Hallazgo("oficina", 999, "e", 1.0),
    })
    assert any(r.codigo == "TABLA_DE_OFICINAS_NO_DISPONIBLE" for r in d.reparos)


# ── Cuando el que falla es el modelo ──

class ModeloQueRevienta:
    """Un modelo que lanza la excepcion que se le dé."""

    nombre = "modelo-caido"

    def __init__(self, excepcion):
        self.excepcion = excepcion

    def generar(self, sistema, mensajes, herramientas):
        raise self.excepcion


async def _sin_esperar(_segundos):
    """Reloj de las pruebas: el reintento no puede costar segundos reales."""


@pytest.mark.asyncio
@pytest.mark.parametrize("excepcion,motivo", [
    (TimeoutError("deadline exceeded"), "MODELO_NO_RESPONDIO"),
    (RuntimeError("503 UNAVAILABLE"), "MODELO_NO_RESPONDIO"),
    (RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded"), "MODELO_NO_RESPONDIO"),
    (RuntimeError("403 PERMISSION_DENIED"), "MODELO_SIN_PERMISO"),
    (ValueError("algo sin clasificar"), "MODELO_NO_RESPONDIO"),
])
async def test_un_fallo_del_modelo_no_se_le_achaca_a_sgdea(tmp_path, excepcion, motivo):
    """
    El motivo viaja en la etiqueta del correo y dirige a quien revisa a mano.
    `SGDEA_CAIDO` en un fallo del modelo lo manda al sistema equivocado, y
    ademas cuenta como fallo de infraestructura de radicacion en el informe.
    """
    orq = Orquestador(modelo=ModeloQueRevienta(excepcion),
                      radicador=RadicadorFalso(), base_workspace=str(tmp_path),
                      dormir=_sin_esperar)
    r = await orq.procesar(_email())

    # El motivo es lo que se comprueba. La accion depende de si el fallo se
    # repara solo: MODELO_NO_RESPONDIO se aplaza, MODELO_SIN_PERMISO no.
    assert r.accion in ("manual", "aplazado")
    assert r.motivo_manual == motivo


@pytest.mark.asyncio
async def test_el_fallo_del_modelo_se_registra_una_sola_vez(tmp_path):
    """
    Lo que no puede repetirse es el REGISTRO del fallo: el manejador general no
    debe volver a clasificarlo despues de que el paso ya lo hizo. Los avisos de
    reintento son otra cosa y sI van uno por intento.
    """
    orq = Orquestador(modelo=ModeloQueRevienta(TimeoutError("deadline exceeded")),
                      radicador=RadicadorFalso(), base_workspace=str(tmp_path),
                      dormir=_sin_esperar)
    r = await orq.procesar(_email())

    registros = [a for a in r.avisos if "en MODELO" in a]
    assert len(registros) == 1, registros
    assert "TIEMPO_AGOTADO" in registros[0]


@pytest.mark.asyncio
async def test_un_fallo_pasajero_del_modelo_se_reintenta(tmp_path):
    """
    Un corte de red de dos minutos mandaba a revision manual todo el lote: el
    job corre cada media hora y nadie los vuelve a mirar.
    """
    esperas = []

    async def reloj(segundos):
        esperas.append(segundos)

    orq = Orquestador(modelo=ModeloQueRevienta(RuntimeError("503 UNAVAILABLE")),
                      radicador=RadicadorFalso(), base_workspace=str(tmp_path),
                      dormir=reloj)
    r = await orq.procesar(_email())

    assert esperas, "no se reintento"
    assert esperas == sorted(esperas), "la espera tiene que ir creciendo"
    assert any("se reintenta" in a for a in r.avisos)
    assert r.motivo_manual == "MODELO_NO_RESPONDIO"


@pytest.mark.asyncio
async def test_un_fallo_definitivo_del_modelo_no_se_reintenta(tmp_path):
    """Sin permiso no hay reintento que valga: reintentarlo gasta el tiempo del job."""
    esperas = []

    async def reloj(segundos):
        esperas.append(segundos)

    orq = Orquestador(modelo=ModeloQueRevienta(RuntimeError("403 PERMISSION_DENIED")),
                      radicador=RadicadorFalso(), base_workspace=str(tmp_path),
                      dormir=reloj)
    r = await orq.procesar(_email())

    assert not esperas
    assert r.motivo_manual == "MODELO_SIN_PERMISO"


def test_todo_motivo_por_paso_esta_en_el_catalogo():
    """Un motivo fuera de MOTIVOS_MANUAL sale como 'motivo no catalogado'."""
    from pipeline.errores import MOTIVO_POR_PASO
    from pipeline.telemetria import MOTIVOS_MANUAL

    for (codigo, paso), motivo in MOTIVO_POR_PASO.items():
        assert motivo in MOTIVOS_MANUAL, f"{codigo}/{paso} -> {motivo}"


@pytest.mark.asyncio
async def test_una_radicacion_sin_numero_y_sin_causa_deja_dicho_que_no_la_hay(
        tmp_path):
    """
    Una radicacion que no devuelve numero y no deja causa: que la causa falte
    es en si mismo el dato que hay que registrar.

    El desenlace es aplazado, no manual: sin causa registrada no se sabe que
    fallo, y eso no autoriza a afirmar que el problema esta en el correo.
    """
    class RadicadorMudo:
        bitacora = None
        suposiciones = ()

        async def radicar(self, email, hallazgos, archivos=None, cuerpo_limpio=""):
            return ""

    orq = Orquestador(modelo=_modelo(), radicador=RadicadorMudo(),
                      base_workspace=str(tmp_path))
    r = await orq.procesar(_email())

    assert r.accion == "aplazado"
    assert r.motivo_manual == "ERROR_INESPERADO"
    assert any("sin registrar la causa" in a for a in r.avisos), r.avisos


# ── El motivo que se reporta describe la causa del desenlace ──

@pytest.mark.parametrize("motivo,lo_decide_el_correo", [
    ("SGDEA_CAIDO", False),
    ("CLOUDFLARE_403", False),
    ("MODELO_NO_RESPONDIO", False),
    ("RADICACION_SIN_CONFIRMAR", False),
    ("TIEMPO_DE_CORRIDA_AGOTADO", False),
    ("ERROR_INESPERADO", False),
    ("SIN_NOMBRE_AFECTADO", True),
    ("DOCUMENTO_AMBIGUO", True),
    ("ADJUNTO_ILEGIBLE", True),
    ("SIN_OCR", True),
    ("", True),
])
def test_se_distingue_quien_decidio_el_desenlace(motivo, lo_decide_el_correo):
    from pipeline.telemetria import lo_decidio_el_contenido

    assert lo_decidio_el_contenido(motivo) is lo_decide_el_correo


def test_el_aviso_de_un_fallo_de_infraestructura_no_manda_la_frase_del_agente():
    """
    La frase del agente dice que dato falto. Un correo aplazado porque el
    backend no respondio salia con "no se pudo identificar la direccion", que
    no es la causa: iba al informe y al correo de aviso interno.
    """
    from pipeline.atribucion import Origen
    from pipeline.orquestador import Orquestador, Resultado

    res = Resultado(email_id="c1", accion="aplazado",
                    motivo_manual="SGDEA_CAIDO")
    res.explicacion_del_agente = "No se pudo identificar la direccion."
    res.atribucion = {"direccion_afectado": Origen.NO_RESUELTO}

    assert Orquestador._detalle_del_aviso(res) == ""


def test_con_un_motivo_del_contenido_si_manda_la_frase_del_agente():
    from pipeline.orquestador import Orquestador, Resultado

    res = Resultado(email_id="c1", accion="manual",
                    motivo_manual="SIN_NOMBRE_AFECTADO")
    res.explicacion_del_agente = "El correo no nombra al trabajador afectado."

    assert Orquestador._detalle_del_aviso(res) == (
        "El correo no nombra al trabajador afectado.")


def test_el_informe_no_muestra_esa_frase_en_un_fallo_de_infraestructura():
    from datetime import datetime
    from pathlib import Path

    from pipeline.telemetria import RegistroCorreo, nueva_ejecucion

    e = nueva_ejecucion(Path("."), id_ejecucion="x",
                        inicio=datetime(2026, 9, 2, 9, 0, 0))
    e.registrar(RegistroCorreo(
        id_correo="AgT1xLrAAA", asunto="Actualizacion de datos",
        accion="aplazado", motivo_manual="SGDEA_CAIDO",
        explicacion="No se pudo identificar la direccion del afectado."))
    texto = e.informe()

    assert "SGDEA_CAIDO" in texto
    assert "por que:" not in texto


# ── Un duplicado sale de la bandeja ──

@pytest.mark.asyncio
async def test_un_duplicado_se_saca_de_la_bandeja(tmp_path):
    """
    Un duplicado que se queda en la bandeja se vuelve a tomar en cada corrida:
    ocupa un cupo del lote, se vuelve a detectar y se vuelve a avisar, sin
    llegar nunca a un desenlace que lo saque de ahi.
    """
    class OutlookQueAnota:
        def __init__(self):
            self.movidos = []
            self.marcas = []

        async def marcar(self, id_correo, marca="", quitar_de_proceso=False):
            self.marcas.append((marca, quitar_de_proceso))
            from pipeline.marcas import fusionar

            return fusionar([], marca, quitar_de_proceso)

        async def move_email(self, id_correo, carpeta):
            self.movidos.append(carpeta)
            return f"{id_correo}-movido"

        async def forward_email(self, id_correo, destino, cuerpo):
            return None

    class DetectorQueDiceDuplicado:
        async def comprobar(self, email):
            from pipeline.duplicados import Veredicto

            return Veredicto(es_duplicado=True, radicado="ENT20260001",
                             motivo="otro correo del mismo hilo ya fue radicado")

        async def comprobar_contenido(self, email, nombres):
            from pipeline.duplicados import Veredicto

            return Veredicto()

        async def registrar_radicado(self, email, nombres):
            return ""

    from config import settings

    outlook = OutlookQueAnota()
    orq = Orquestador(modelo=_modelo(), outlook=outlook,
                      radicador=RadicadorFalso(),
                      base_workspace=str(tmp_path))
    orq.duplicados = DetectorQueDiceDuplicado()
    r = await orq.procesar(_email())

    assert r.accion == "duplicado"
    assert outlook.movidos == [settings.OUTLOOK_FOLDER_DUPLICADOS], outlook.movidos
    # Se retiran las marcas de proceso: la del radicado ya identifica el correo.
    assert outlook.marcas and outlook.marcas[-1][1] is True


# ── El resto del hilo sale con el correo radicado ──

class OutlookConHilo:
    """Bandeja con tres correos mas en el hilo del que se procesa."""

    def __init__(self, falla_listar=False):
        self.movidos = []
        self.falla_listar = falla_listar

    async def marcar(self, id_correo, marca="", quitar_de_proceso=False):
        from pipeline.marcas import fusionar

        return fusionar([], marca, quitar_de_proceso)

    async def move_email(self, id_correo, carpeta):
        self.movidos.append((id_correo, carpeta))
        return f"{id_correo}-movido"

    async def get_conversation_message_ids(self, carpeta, conversacion):
        if self.falla_listar:
            raise RuntimeError("Graph no responde")
        assert conversacion == "hilo-1"
        return ["c2", "c3", "c4"]

    async def forward_email(self, id_correo, destino, cuerpo):
        return None


class DetectorQueDejaPasar:
    async def comprobar(self, email):
        from pipeline.duplicados import Veredicto

        return Veredicto()

    async def comprobar_contenido(self, email, nombres):
        from pipeline.duplicados import Veredicto

        return Veredicto()

    async def registrar_radicado(self, email, nombres):
        return ""


@pytest.mark.asyncio
async def test_al_radicar_el_resto_del_hilo_sale_como_duplicado(tmp_path, monkeypatch):
    """
    La bandeja da un correo por hilo en cada corrida: si el resto del hilo se
    queda, sale un duplicado por corrida durante horas.
    """
    from config import settings

    outlook = OutlookConHilo()
    orq = Orquestador(modelo=_modelo(), outlook=outlook,
                      radicador=RadicadorFalso(), base_workspace=str(tmp_path))
    orq.duplicados = DetectorQueDejaPasar()
    monkeypatch.setattr(orq, "_avisar", lambda *a, **k: _nada())
    r = await orq.procesar(_email())

    assert r.accion == "radicado", (r.accion, r.motivo_manual, r.avisos)
    assert outlook.movidos[0] == ("c1", settings.OUTLOOK_FOLDER_RADICACION_EXITOSA)
    assert outlook.movidos[1:] == [
        (i, settings.OUTLOOK_FOLDER_DUPLICADOS) for i in ("c2", "c3", "c4")]
    assert any("3 correo(s) mas del mismo hilo" in a for a in r.avisos), r.avisos


async def _nada():
    return None


@pytest.mark.asyncio
async def test_si_no_se_puede_listar_el_hilo_queda_constancia(tmp_path, monkeypatch):
    outlook = OutlookConHilo(falla_listar=True)
    orq = Orquestador(modelo=_modelo(), outlook=outlook,
                      radicador=RadicadorFalso(), base_workspace=str(tmp_path))
    orq.duplicados = DetectorQueDejaPasar()
    monkeypatch.setattr(orq, "_avisar", lambda *a, **k: _nada())
    r = await orq.procesar(_email())

    assert r.accion == "radicado"
    assert len(outlook.movidos) == 1
    assert any("no se pudo listar el resto del hilo" in a for a in r.avisos), r.avisos


@pytest.mark.asyncio
async def test_un_correo_a_revision_manual_no_arrastra_su_hilo(tmp_path):
    """Sin radicado en el hilo, los demas correos tienen que procesarse."""
    outlook = OutlookConHilo()
    orq = Orquestador(modelo=_modelo(), outlook=outlook,
                      radicador=RadicadorFalso(), base_workspace=str(tmp_path))
    await orq._a_revision_manual(_email(), "SIN_DOCUMENTO", [])

    assert len(outlook.movidos) == 1
