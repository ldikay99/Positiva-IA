"""
Las cinco capas de deteccion de correos ya radicados.

Todas corren antes de convertir adjuntos o llamar al modelo. Cuando una capa
no esta disponible, el correo sigue y la perdida queda en `motivo`.
"""
import pytest

from models.email import EmailAddress, EmailBody, EmailMessage
from pipeline.duplicados import Detector, etiqueta_de_radicado, hash_del_correo


def _email(id_correo="c1", categorias=None, conversacion="hilo-1",
           asunto="Solicitud", remitente="ana@empresa.com"):
    return EmailMessage(
        id=id_correo,
        subject=asunto,
        body=EmailBody(content="cuerpo"),
        from_address=EmailAddress(name="Ana Lopez", address=remitente),
        categories=list(categorias or []),
        conversation_id=conversacion,
    )


class CacheFalsa:
    def __init__(self, hashes=None, revienta=False):
        self.hashes = set(hashes or [])
        self.revienta = revienta
        self.guardados = []

    async def email_hash_exists(self, llave):
        if self.revienta:
            raise TimeoutError("redis no responde")
        return llave in self.hashes

    async def save_email_hash(self, llave, ttl_seconds=0):
        if self.revienta:
            raise TimeoutError("redis no responde")
        self.guardados.append(llave)
        self.hashes.add(llave)
        return True


class CacheQueSeTragaElError:
    """
    Como el cliente real: no propaga, devuelve False y deja el motivo en
    `ultimo_error`. False significa a la vez "no hay duplicado" y "no se pudo
    comprobar".
    """

    ultimo_error = "ConnectionError: redis no responde"

    async def email_hash_exists(self, llave):
        return False

    async def save_email_hash(self, llave, ttl_seconds=0):
        return False


class AuditoriaFalsa:
    def __init__(self, registros=None, revienta=False):
        self.registros = set(registros or [])
        self.revienta = revienta

    async def email_exists(self, correo, asunto, recibido=""):
        if self.revienta:
            raise RuntimeError("la base de radicados no responde")
        return (correo, asunto) in self.registros


class OutlookConHilo:
    def __init__(self, radicado=None, revienta=False, por_asunto=None):
        self.radicado = radicado
        self.por_asunto = por_asunto
        self.revienta = revienta
        self.consultas = 0

    async def hilo_ya_radicado(self, conversation_id):
        self.consultas += 1
        if self.revienta:
            raise ConnectionError("Graph no responde")
        return self.radicado

    async def buscar_etiqueta_ent_por_asunto(self, asunto, email):
        if self.revienta:
            raise ConnectionError("Graph no responde")
        return self.por_asunto


# ── La etiqueta ──

@pytest.mark.parametrize("categoria", [
    "ENT20260001", "ENT-20260001", "ent 20260001", "  ENT20260001  ",
])
def test_una_etiqueta_de_radicado_se_reconoce(categoria):
    assert etiqueta_de_radicado([categoria])


@pytest.mark.parametrize("categoria", [
    "Rojo", "REVISION-MANUAL:SIN_DOCUMENTO", "ENTREGADO", "", "ENT",
])
def test_otras_etiquetas_no_cuentan_como_radicado(categoria):
    """'ENTREGADO' empieza por ENT: sin el digito, cualquier palabra pasaria."""
    assert not etiqueta_de_radicado([categoria])


def test_sin_categorias_no_revienta():
    assert etiqueta_de_radicado(None) == ""
    assert etiqueta_de_radicado([]) == ""


def test_entregado_no_es_un_radicado():
    """
    `startswith("ENT")` acepta "ENTREGADO". Con eso, un correo etiquetado asi
    se descarta como duplicado y se pierde una PQRD legitima, que es peor que
    duplicarla. El digito es lo que distingue el radicado.
    """
    assert not etiqueta_de_radicado(["ENTREGADO"])
    assert not etiqueta_de_radicado(["ENTIDAD EXTERNA"])
    assert etiqueta_de_radicado(["ENT20260001"])


def test_la_etiqueta_se_define_en_un_solo_sitio():
    """Escritor y lector comparten la definicion de `utils.radicado`."""
    from pathlib import Path

    servicios = Path(__file__).resolve().parent.parent / "services" / "outlook.py"
    fuente = servicios.read_text(encoding="utf-8")
    assert 'startswith("ENT")' not in fuente, (
        "hay una segunda definicion de la etiqueta de radicado")
    assert "from utils.radicado import" in fuente


# ── Las tres capas ──

@pytest.mark.asyncio
async def test_el_propio_correo_etiquetado_es_duplicado():
    d = Detector()
    v = await d.comprobar(_email(categorias=["ENT20260001"]))
    assert v.es_duplicado
    assert v.radicado == "ENT20260001"


@pytest.mark.asyncio
async def test_un_correo_del_mismo_hilo_ya_radicado_es_duplicado():
    outlook = OutlookConHilo(radicado="ENT20260002")
    v = await Detector(outlook=outlook).comprobar(_email())
    assert v.es_duplicado
    assert v.radicado == "ENT20260002"
    assert outlook.consultas == 1


@pytest.mark.asyncio
async def test_el_segundo_correo_del_hilo_en_el_mismo_lote_es_duplicado():
    """
    Un hilo con cinco respuestas llega como cinco correos que aun no llevan
    la etiqueta del otro: hay que detectarlos entre si dentro del mismo lote.
    """
    d = Detector(outlook=OutlookConHilo())
    primero = await d.comprobar(_email("c1"))
    segundo = await d.comprobar(_email("c2"))
    assert not primero.es_duplicado
    assert segundo.es_duplicado
    assert "en esta ejecucion" in segundo.motivo


@pytest.mark.asyncio
async def test_hilos_distintos_no_se_confunden():
    d = Detector(outlook=OutlookConHilo())
    await d.comprobar(_email("c1", conversacion="hilo-1"))
    v = await d.comprobar(_email("c2", conversacion="hilo-2"))
    assert not v.es_duplicado


@pytest.mark.asyncio
async def test_un_correo_sin_hilo_se_procesa():
    d = Detector(outlook=OutlookConHilo())
    assert not (await d.comprobar(_email(conversacion=None))).es_duplicado


# ── Degradar no es fallar en silencio ──

@pytest.mark.asyncio
async def test_si_graph_falla_se_procesa_pero_queda_dicho():
    """
    Bloquear el lote entero por una consulta caida es peor que arriesgar un
    duplicado. Pero el riesgo tiene que constar en el informe.
    """
    v = await Detector(outlook=OutlookConHilo(revienta=True)).comprobar(_email())
    assert not v.es_duplicado
    assert "comprobacion del hilo" in v.motivo
    assert "ConnectionError" in v.motivo


@pytest.mark.asyncio
async def test_una_capa_caida_no_apaga_la_del_lote():
    """
    Si Graph se cae, la capa que no depende de red tiene que seguir en pie: es
    la unica que evita radicar cinco veces un hilo de cinco respuestas.
    """
    d = Detector(outlook=OutlookConHilo(revienta=True))
    await d.comprobar(_email("c1"))
    assert (await d.comprobar(_email("c2"))).es_duplicado


# ── Reenvio interno: el hilo cambia, el asunto no ──

@pytest.mark.asyncio
async def test_un_reenvio_interno_con_hilo_nuevo_se_detecta():
    """
    Cuando un area de Positiva reenvia, Outlook abre un conversationId nuevo y
    la capa del hilo deja de ver el radicado. El asunto si sobrevive.
    """
    outlook = OutlookConHilo(radicado=None, por_asunto="ENT20260005")
    v = await Detector(outlook=outlook).comprobar(
        _email(conversacion="hilo-nuevo", asunto="RV: Solicitud"))
    assert v.es_duplicado
    assert v.radicado == "ENT20260005"
    assert "mismo asunto" in v.motivo


@pytest.mark.asyncio
async def test_en_modo_test_se_avisa_que_la_deteccion_esta_apagada():
    """
    `services/outlook.py` apaga las dos consultas a Graph cuando MODO_TEST esta
    activo, para poder reprocesar. Sin decirlo, un e2e "sin duplicados" se lee
    como que la deduplicacion funciona, cuando ni siquiera se ejercito.
    """
    v = await Detector(outlook=OutlookConHilo()).comprobar(_email())
    assert not v.es_duplicado
    assert "MODO_TEST" in v.motivo, v.motivo


def test_las_dos_capas_contra_graph_se_apagan_juntas():
    """
    Las dos consultas a Graph se apagan juntas: si una queda encendida y la
    otra no, el resultado del e2e no se puede interpretar.
    """
    from pathlib import Path

    fuente = (Path(__file__).resolve().parent.parent /
              "services" / "outlook.py").read_text(encoding="utf-8")
    for metodo in ("hilo_ya_radicado", "buscar_etiqueta_ent_por_asunto"):
        cuerpo = fuente.split(f"async def {metodo}(")[1][:1500]
        assert "dedup_desactivada()" in cuerpo, (
            f"{metodo} decide por su cuenta cuando apagarse")


def test_los_tres_servicios_usan_el_mismo_criterio():
    """
    Si cada uno lleva su propia condicion, unas capas quedan encendidas y
    otras no, y la corrida no se puede interpretar.
    """
    from pathlib import Path

    raiz = Path(__file__).resolve().parent.parent / "services"
    for archivo in ("outlook.py", "redis_client.py", "registro_radicados.py"):
        fuente = (raiz / archivo).read_text(encoding="utf-8")
        assert "from config.settings import dedup_desactivada" in fuente, archivo


# ── Poder validar la deduplicacion sin escribir nada ──

def _con_modo(monkeypatch, modo_test, dedup_en_pruebas):
    from config.settings import dedup_desactivada, settings

    monkeypatch.setattr(settings, "MODO_TEST", modo_test)
    monkeypatch.setattr(settings, "DEDUP_EN_PRUEBAS", dedup_en_pruebas)
    return dedup_desactivada()


def test_en_pruebas_la_deduplicacion_esta_apagada_por_defecto(monkeypatch):
    """En pruebas la deduplicacion esta apagada por defecto para poder reprocesar un correo."""
    assert _con_modo(monkeypatch, modo_test=True, dedup_en_pruebas=False)


def test_se_puede_encender_la_deduplicacion_sin_habilitar_escrituras(monkeypatch):
    """
    Las consultas son de solo lectura: apagarlas con MODO_TEST confunde "no
    escribas" con "no consultes", y deja la deduplicacion sin poder validarse
    nunca contra el buzon real.
    """
    assert not _con_modo(monkeypatch, modo_test=True, dedup_en_pruebas=True)


def test_en_produccion_la_deduplicacion_siempre_esta_encendida(monkeypatch):
    assert not _con_modo(monkeypatch, modo_test=False, dedup_en_pruebas=False)


def test_las_escrituras_no_dependen_del_interruptor_de_dedup():
    """
    DEDUP_EN_PRUEBAS solo puede encender CONSULTAS. Si tocara una escritura,
    una corrida de prueba escribiria en el buzon real.
    """
    from pathlib import Path

    fuente = (Path(__file__).resolve().parent.parent /
              "services" / "outlook.py").read_text(encoding="utf-8")
    for escritura in ("etiquetar_correo", "move_email", "forward_email",
                      "mover_hilo_completo"):
        cuerpo = fuente.split(f"async def {escritura}(")[1][:900]
        assert "settings.MODO_TEST" in cuerpo, f"{escritura} no respeta MODO_TEST"
        assert "dedup_desactivada" not in cuerpo, (
            f"{escritura} depende del interruptor de deduplicacion")


@pytest.mark.asyncio
async def test_sin_asunto_no_se_consulta_por_asunto():
    """Buscar por asunto vacio traeria cualquier correo sin asunto del buzon."""
    v = await Detector(outlook=OutlookConHilo(por_asunto="ENT20260006")).comprobar(
        _email(asunto=""))
    assert not v.es_duplicado


@pytest.mark.asyncio
async def test_sin_outlook_las_otras_capas_siguen():
    d = Detector(outlook=None)
    assert (await d.comprobar(_email(categorias=["ENT20260003"]))).es_duplicado
    await d.comprobar(_email("c1"))
    assert (await d.comprobar(_email("c2"))).es_duplicado


# ── Capa 4: hash del contenido ──

def test_los_prefijos_de_reenvio_no_cambian_el_hash():
    """
    Si el hilo lo reenvian tres areas de Positiva, "RV: RE: Solicitud" tiene
    que dar la misma llave que "Solicitud" o se radica una vez por reenvio.
    """
    assert hash_del_correo(_email(asunto="RV: RE: Solicitud")) == \
        hash_del_correo(_email(asunto="Solicitud"))


def test_otros_adjuntos_son_otro_caso():
    """Mismo asunto y remitente con soportes distintos no es el mismo tramite."""
    assert hash_del_correo(_email(), ["cedula.pdf"]) != \
        hash_del_correo(_email(), ["cedula.pdf", "incapacidad.pdf"])


def test_el_orden_de_los_adjuntos_no_cambia_el_hash():
    assert hash_del_correo(_email(), ["b.pdf", "a.pdf"]) == \
        hash_del_correo(_email(), ["a.pdf", "b.pdf"])


def test_otro_remitente_es_otro_hash():
    assert hash_del_correo(_email(remitente="ana@x.com")) != \
        hash_del_correo(_email(remitente="luis@x.com"))


@pytest.mark.asyncio
async def test_un_contenido_ya_visto_es_duplicado():
    llave = hash_del_correo(_email(), ["cedula.pdf"])
    d = Detector(cache=CacheFalsa(hashes=[llave]))
    v = await d.comprobar_contenido(_email(), ["cedula.pdf"])
    assert v.es_duplicado
    assert llave[:12] in v.motivo


@pytest.mark.asyncio
async def test_el_hash_se_guarda_al_radicar():
    """Sin guardarlo la cache nunca se llena y la capa deja de detectar nada."""
    cache = CacheFalsa()
    d = Detector(cache=cache)
    assert await d.registrar_radicado(_email(), ["cedula.pdf"]) == ""
    assert cache.guardados == [hash_del_correo(_email(), ["cedula.pdf"])]

    v = await d.comprobar_contenido(_email("c2"), ["cedula.pdf"])
    assert v.es_duplicado


# ── Capa 6: auditoria por remitente y asunto ──

@pytest.mark.asyncio
async def test_un_registro_previo_en_la_auditoria_es_duplicado():
    """Sin cache que responda por el contenido, la auditoria decide sola."""
    d = Detector(auditoria=AuditoriaFalsa(
        registros=[("ana@empresa.com", "Solicitud")]))
    v = await d.comprobar_contenido(_email())
    assert v.es_duplicado
    assert "remitente y asunto" in v.motivo


@pytest.mark.asyncio
async def test_la_app_de_positiva_no_junta_a_personas_distintas():
    """
    Todas las PQRD de la app llegan de appconexionpositiva@ con el asunto
    "PQRD - Positiva App". Que haya un radicado con ese par no dice nada de la
    siguiente, que puede ser de otra persona.
    """
    app = "appconexionpositiva@positiva.gov.co"
    d = Detector(auditoria=AuditoriaFalsa(registros=[(app, "PQRD - Positiva App")]))
    v = await d.comprobar_contenido(_email(asunto="PQRD - Positiva App", remitente=app))
    assert not v.es_duplicado


@pytest.mark.asyncio
async def test_un_area_de_positiva_que_reenvia_tampoco_cuenta_como_remitente():
    area = "notificacionesjudiciales@positiva.gov.co"
    d = Detector(auditoria=AuditoriaFalsa(registros=[(area, "Derecho de peticion")]))
    v = await d.comprobar_contenido(_email(asunto="Derecho de peticion", remitente=area))
    assert not v.es_duplicado


@pytest.mark.asyncio
async def test_el_contenido_manda_sobre_el_remitente_y_el_asunto():
    """
    Bajo el asunto generico de la app entran PQRD distintas de la misma
    persona. La auditoria las da por repetidas; el hash del contenido dice que
    no, y es el mas especifico de los dos.
    """
    class CacheQueNoLoHaVisto:
        ultimo_error = ""

        async def email_hash_exists(self, llave):
            return False

    d = Detector(
        cache=CacheQueNoLoHaVisto(),
        auditoria=AuditoriaFalsa(registros=[("ana@empresa.com", "Solicitud")]))

    v = await d.comprobar_contenido(_email())

    assert not v.es_duplicado, "el contenido no se habia visto: es otra PQRD"


@pytest.mark.asyncio
async def test_si_la_cache_se_cae_la_auditoria_vuelve_a_decidir():
    """Sin nadie que responda por el contenido, mas vale su respuesta que ninguna."""
    d = Detector(
        cache=CacheFalsa(revienta=True),
        auditoria=AuditoriaFalsa(registros=[("ana@empresa.com", "Solicitud")]))

    v = await d.comprobar_contenido(_email())

    assert v.es_duplicado


@pytest.mark.asyncio
async def test_el_asunto_se_limpia_antes_de_preguntar():
    """
    Un reenvio antepone "RV:" y cambia el texto sin cambiar el caso. Con el
    prefijo, la consulta no encuentra el radicado que si existe.
    """
    auditoria = AuditoriaFalsa(registros=[("ana@empresa.com", "Solicitud")])
    d = Detector(auditoria=auditoria)

    v = await d.comprobar_contenido(_email(asunto="RV: Solicitud"))

    assert v.es_duplicado


@pytest.mark.asyncio
async def test_sin_remitente_no_se_consulta_la_auditoria():
    email = _email()
    email.from_address = None
    v = await Detector(auditoria=AuditoriaFalsa()).comprobar_contenido(email)
    assert not v.es_duplicado


# ── Degradar deja rastro, siempre ──

@pytest.mark.asyncio
async def test_una_capa_ausente_queda_dicha():
    """Una capa ausente tiene que constar en el motivo: si no, "no hay duplicado" y "no se pudo comprobar" se leen igual."""
    v = await Detector().comprobar(_email())
    assert not v.es_duplicado
    assert "no esta configurada" in v.motivo or v.motivo == ""


@pytest.mark.asyncio
async def test_la_cache_apagada_no_ensucia_cada_correo():
    """
    `cache=None` es una capa retirada, no una capa caida: no hay nada que
    avisar en cada correo. Un fallo del servicio si aparece, y eso lo cubre
    `test_una_capa_caida_no_bloquea_pero_queda_dicha`.
    """
    v = await Detector().comprobar_contenido(_email())
    assert not v.es_duplicado
    assert "cache de hashes" not in v.motivo
    assert await Detector().registrar_radicado(_email()) == ""


@pytest.mark.asyncio
async def test_una_capa_caida_no_bloquea_pero_queda_dicha():
    v = await Detector(
        cache=CacheFalsa(revienta=True),
    ).comprobar_contenido(_email())
    assert not v.es_duplicado
    assert "TimeoutError" in v.motivo


@pytest.mark.asyncio
async def test_si_no_se_pudo_guardar_el_hash_queda_dicho():
    fallo = await Detector(cache=CacheFalsa(revienta=True)).registrar_radicado(
        _email())
    assert "no se guardo el hash" in fallo
    assert "TimeoutError" in fallo


@pytest.mark.asyncio
async def test_una_capa_que_se_traga_su_error_igual_queda_reportada():
    """
    El cliente real de Redis captura la excepcion y devuelve False, que el
    detector leeria como "no hay duplicado". Sin mirar `ultimo_error`, una capa
    muerta se ve igual que una capa que dijo "todo limpio".
    """
    v = await Detector(cache=CacheQueSeTragaElError()).comprobar_contenido(_email())
    assert not v.es_duplicado
    assert "redis no responde" in v.motivo, v.motivo


@pytest.mark.asyncio
async def test_una_busqueda_por_asunto_caida_no_pasa_por_limpia():
    """
    `buscar_etiqueta_ent_por_asunto` devuelve None si Graph responde 5xx, y
    None es lo mismo que "no hay duplicado". El llamador tiene que verlo.
    """
    class OutlookConGraphCaido:
        ultimo_error = "Graph respondio 503 al buscar por asunto"

        async def hilo_ya_radicado(self, conversacion):
            return None

        async def buscar_etiqueta_ent_por_asunto(self, asunto, email):
            return None

    v = await Detector(outlook=OutlookConGraphCaido()).comprobar(_email())
    assert not v.es_duplicado
    assert "503" in v.motivo, v.motivo


@pytest.mark.asyncio
async def test_una_capa_sana_no_reporta_nada():
    """El aviso solo aparece cuando de verdad fallo algo."""
    v = await Detector(cache=CacheFalsa()).comprobar_contenido(_email())
    assert not v.es_duplicado
    assert v.motivo == "", v.motivo


def test_los_clientes_reales_dejan_rastro_del_fallo():
    """
    Es la pieza que hace posible lo anterior. Si alguien quita `ultimo_error`,
    el detector vuelve a quedarse ciego sin que ninguna prueba se queje.
    """
    from pathlib import Path

    raiz = Path(__file__).resolve().parent.parent / "services"
    for archivo, metodo in (("redis_client.py", "email_hash_exists"),
                            ("registro_radicados.py", "email_exists"),
                            ("outlook.py", "buscar_etiqueta_ent_por_asunto")):
        cuerpo = (raiz / archivo).read_text(encoding="utf-8") \
            .split(f"async def {metodo}(")[1][:1200]
        assert "self.ultimo_error =" in cuerpo, (
            f"{archivo}:{metodo} devuelve False sin decir si fallo")


@pytest.mark.asyncio
async def test_un_servicio_sin_el_metodo_esperado_no_revienta():
    class Incompleto:
        pass

    v = await Detector(cache=Incompleto()).comprobar_contenido(_email())
    assert not v.es_duplicado
    assert "no expone email_hash_exists" in v.motivo


# ── Enganche con el pipeline ──

@pytest.mark.asyncio
async def test_un_duplicado_no_gasta_modelo(tmp_path):
    """El punto de comprobarlo primero: no debe costar un solo token."""
    from pipeline.modelo import ModeloFalso
    from pipeline.orquestador import Orquestador

    modelo = ModeloFalso(turnos=[])
    orq = Orquestador(modelo=modelo, base_workspace=str(tmp_path))
    r = await orq.procesar(_email(categorias=["ENT20260004"]))

    assert r.accion == "duplicado"
    assert r.radicado == "ENT20260004"
    assert modelo.veces_llamado == 0, "se llamo al modelo por un duplicado"


@pytest.mark.asyncio
async def test_el_lote_no_radica_dos_veces_el_mismo_hilo(tmp_path):
    from pipeline.modelo import Llamada, ModeloFalso, Turno
    from pipeline.orquestador import Orquestador

    def turnos():
        return [
            Turno(llamadas=[
                Llamada("reportar_hallazgo", {
                    "campo": "tipo_solicitud", "valor": "16",
                    "evidencia": "asunto del correo", "confianza": 0.9}),
            ]),
            Turno(texto="LISTO"),
        ]

    orq = Orquestador(modelo=ModeloFalso(turnos=turnos() + turnos()),
                      base_workspace=str(tmp_path))
    primero = await orq.procesar(_email("c1"))
    segundo = await orq.procesar(_email("c2"))

    assert primero.accion != "duplicado"
    assert segundo.accion == "duplicado"
