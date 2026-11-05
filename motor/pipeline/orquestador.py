"""Pipeline completo: de un correo de Outlook a una radicacion en SGDEA."""
from __future__ import annotations

import asyncio
import contextvars
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config import settings
from pipeline.atribucion import Origen
from models.email import EmailMessage
from pipeline import compuerta, facturas, volumen
from pipeline.agente import (
    ESQUEMAS_HERRAMIENTAS,
    atribucion,
    combinar,
    resolver,
)
from pipeline.compuerta import Veredicto
from pipeline.duplicados import Detector
from pipeline.enrutamiento import (
    MOTIVO_REMITENTE_EXCLUIDO,
    MOTIVO_REMITENTE_NO_IDENTIFICABLE,
    Destino,
    decidir,
)
from pipeline.errores import (
    Bitacora,
    FalloDelModelo,
    clasificar,
    debe_reintentar,
    espera_para,
)
from pipeline.extraccion import Hallazgo, extraer_todo
from pipeline.materializar import materializar
from pipeline.modelo import Modelo
from pipeline import notificacion, reclamo, reintentos
from pipeline.marcas import OtraCorridaLoTomo, ReclamoNoEscrito
from pipeline.presupuesto import Presupuesto
from pipeline.personas import ConsultaDePersonaFallo, rellenar_desde_el_registro
from pipeline.salida import AVISO, datos_del_correo, emitir
from pipeline.saneamiento import Severidad, detectar
from pipeline.telemetria import (
    Ejecucion,
    RegistroCorreo,
    explica_el_desenlace,
)
from servidor_mcp.herramientas import Contexto
from utils.generic_filter import (
    es_buzon_de_positiva,
    tiene_remitente_externo_identificable,
)
from utils.html_cleaner import clean_html_content
from utils.radicado import etiqueta_para


@dataclass
class Resultado:
    email_id: str
    asunto: str = ""
    # radicado | duplicado | reenviado | aplazado | manual | error
    accion: str = "error"
    radicado: str = ""
    motivo_manual: str = ""
    hallazgos: Dict[str, Hallazgo] = field(default_factory=dict)
    atribucion: Dict[str, Origen] = field(default_factory=dict)
    avisos: List[str] = field(default_factory=list)
    duracion_ms: int = 0
    etapas_ms: Dict[str, int] = field(default_factory=dict)
    tokens_entrada: int = 0
    tokens_salida: int = 0
    # Vueltas que dio el agente, una invocacion al modelo cada una. Cero cuando
    # la capa determinista resolvio todo y no se le llamo.
    turnos_del_agente: int = 0
    # La frase con la que el agente explico que dato falto. Vacia cuando no se
    # le llamo o cuando resolvio todo lo que se le pidio.
    explicacion_del_agente: str = ""
    # Area interna a la que se remitio el correo, y en que se reconocio que le
    # correspondia. Solo con `accion == "reenviado"`. Sin esto, un reenvio sale
    # sin decir a donde fue: no tiene numero de radicado ni motivo de revision
    # manual, que es lo que llevan los demas desenlaces.
    area_destino: str = ""
    razon_del_reenvio: str = ""
    # Que capa de deduplicacion lo detecto. Solo con `accion == "duplicado"`.
    razon_del_duplicado: str = ""

    # Se llama al terminar cada etapa, con (correo, etapa, ms, fallo). Sirve
    # para que la consola muestre el avance paso a paso: con una sola linea por
    # correo, un fallo a mitad no dice en que etapa ocurrio.
    traza: Any = None

    def cronometrar(self, etapa: str):
        """Mide una etapa. Se usa como `with res.cronometrar("adjuntos"):`."""
        return _Cronometro(self.etapas_ms, etapa, self.traza, self.email_id)


class _Cronometro:
    def __init__(self, destino: Dict[str, int], etapa: str,
                 traza: Any = None, correo: str = "") -> None:
        self.destino = destino
        self.etapa = etapa
        self.traza = traza
        self.correo = correo

    def __enter__(self):
        self.inicio = time.time()
        return self

    def __exit__(self, tipo, valor, _traza):
        # Se acumula: una etapa puede entrar mas de una vez por correo.
        transcurrido = int((time.time() - self.inicio) * 1000)
        self.destino[self.etapa] = self.destino.get(self.etapa, 0) + transcurrido
        if self.traza is not None:
            # La etapa se anuncia tambien cuando revienta: es justo el caso en
            # que hace falta saber donde se quedo.
            self.traza(self.correo, self.etapa, transcurrido,
                       f"{tipo.__name__}: {valor}" if tipo else "")
        return False


# Bitacora del correo que se esta procesando en ESTA tarea. Va en un
# `ContextVar` y no en el orquestador porque el mismo orquestador atiende varios
# correos a la vez: como atributo, los fallos de uno acabarian en el informe de
# otro. El agente la alcanza desde una herramienta, fuera de la pila de
# `procesar`, y por eso no se puede pasar por parametro.
_BITACORA = contextvars.ContextVar("bitacora_del_correo", default=None)


def _bitacora() -> Bitacora:
    """
    La bitacora del correo que atiende esta tarea.

    Devuelve una vacia si nadie la fijo, para que una herramienta invocada
    fuera de `procesar` registre en algun sitio en vez de reventar.
    """
    actual = _BITACORA.get()
    if actual is None:
        actual = Bitacora()
        _BITACORA.set(actual)
    return actual


class Orquestador:
    """
    Ejecuta el pipeline sobre un correo.

    Los servicios se inyectan para poder correr sin red en las pruebas. El
    orden es fijo: materializar, sanear, extraer, resolver, validar, radicar.

    Una misma instancia atiende varios correos a la vez: lo que es de un correo
    vive en variables locales o en `_BITACORA`, nunca en `self`.
    """

    def __init__(
        self,
        modelo: Modelo,
        outlook: Any = None,
        radicador: Any = None,
        sgdea: Any = None,
        base_workspace: Optional[str] = None,
        ejecucion: Optional[Ejecucion] = None,
        cache: Any = None,
        auditoria: Any = None,
        dormir: Any = None,
        traza: Any = None,
        presupuesto: Optional[Presupuesto] = None,
    ) -> None:
        self.modelo = modelo
        self.outlook = outlook
        self.radicador = radicador
        self.sgdea = sgdea
        self.base_workspace = base_workspace
        self.ejecucion = ejecucion
        # Sin presupuesto no hay tope de corrida: es lo que quieren las pruebas
        # y una ejecucion a mano. En el Job siempre se pasa uno.
        self.presupuesto = presupuesto
        # Se inyecta para que las pruebas del reintento no esperen de verdad.
        self.dormir = dormir or asyncio.sleep
        # Sin traza no se imprime nada: las pruebas no la pasan.
        self.traza = traza
        # Vive en el orquestador, no en `procesar`: recuerda los hilos ya
        # atendidos durante todo el lote.
        self.duplicados = Detector(
            outlook=outlook, cache=cache, auditoria=auditoria)

    # ── Entrada ──

    async def procesar(self, email: EmailMessage) -> Resultado:
        inicio = time.time()
        res = Resultado(email_id=email.id, asunto=email.subject or "",
                        traza=self.traza)
        bitacora = Bitacora()
        # `_consultar_persona` la llama el agente a traves de una herramienta,
        # fuera de esta pila: necesita alcanzarla sin pasarla por parametro.
        _BITACORA.set(bitacora)

        try:
            # Capas de deduplicacion que no necesitan los adjuntos.
            with res.cronometrar("deduplicacion"):
                repetido = await self.duplicados.comprobar(email)
            if repetido.motivo and not repetido.es_duplicado:
                res.avisos.append(f"deduplicacion degradada: {repetido.motivo}")
            if repetido.es_duplicado:
                res.accion = "duplicado"
                res.radicado = repetido.radicado
                res.razon_del_duplicado = repetido.motivo
                res.avisos.append(repetido.motivo)
                await self._archivar_duplicado(email, res.avisos)
                return await self._cerrar(res, inicio, bitacora, email)

            # Reclamar antes de trabajar. Las capas de arriba son de lectura y
            # se apoyan en marcas que se escriben al final; entre radicar y
            # etiquetar cabe otra corrida entera, y por ahi sale el mismo caso
            # radicado dos veces.
            trabajar, intento, explicacion = reclamo.decidir(
                getattr(email, "categories", None))
            if explicacion:
                res.avisos.append(explicacion)
            if not trabajar:
                res.accion = "manual"
                res.motivo_manual = "RECLAMO_AGOTADO"
                await self._a_revision_manual(
                    email, res.motivo_manual, res.avisos, res)
                return await self._cerrar(res, inicio, bitacora, email)
            try:
                await self._marcar(email, reclamo.marca(intento),
                                   avisos=res.avisos, obligatoria=True)
            except OtraCorridaLoTomo as e:
                # No se ha tocado nada del correo todavia. Se deja como esta:
                # la corrida que gano la reclamacion lo esta trabajando y es
                # ella la que avisa y la que radica.
                res.accion = "en_proceso"
                res.avisos.append(str(e))
                return await self._cerrar(res, inicio, bitacora, email)
            except ReclamoNoEscrito as e:
                # El correo se queda tal como llego, sin ninguna marca. Es el
                # unico desenlace que no puede producir un duplicado: la corrida
                # siguiente lo ve como nuevo y vuelve a intentar reclamarlo.
                res.accion = "sin_reclamar"
                res.avisos.append(str(e))
                return await self._cerrar(res, inicio, bitacora, email)

            cuerpo = clean_html_content(email.body.content or "")

            # Antes de los adjuntos y del modelo: un correo que se reenvia no
            # necesita ni OCR ni agente.
            with res.cronometrar("enrutamiento"):
                ruta = decidir(
                    asunto=email.subject or "",
                    cuerpo=cuerpo,
                    correo_remitente=(email.from_address.address
                                      if email.from_address else ""),
                )
            # Antes del enrutamiento: una factura no es de ningun area, asi
            # que reenviarla seria mandarle a otro buzon algo que tampoco le
            # toca. Y antes de los adjuntos, que no hace falta abrir.
            razon = facturas.es_factura(email.subject or "", cuerpo)
            if razon:
                res.accion = "manual"
                res.motivo_manual = facturas.MOTIVO
                res.avisos.append(razon)
                await self._a_revision_manual(
                    email, res.motivo_manual, res.avisos, res)
                return await self._cerrar(res, inicio, bitacora, email)

            if ruta.destino is Destino.NO_SE_RADICA:
                # No se reenvia: devolverselo a quien lo escribio lo deja
                # rebotando. Queda para que lo mire una persona.
                res.accion = "manual"
                res.motivo_manual = MOTIVO_REMITENTE_EXCLUIDO
                res.avisos.append(ruta.evidencia)
                await self._a_revision_manual(
                    email, res.motivo_manual, res.avisos, res)
                return await self._cerrar(res, inicio, bitacora, email)

            if ruta.se_reenvia:
                res.accion = "reenviado"
                res.motivo_manual = ""
                res.area_destino = ruta.nombre_del_area or ruta.correo_destino
                res.razon_del_reenvio = ruta.evidencia
                res.avisos.append(
                    f"reenviado a {ruta.correo_destino}: {ruta.evidencia}")
                await self._reenviar(email, ruta, res.avisos)
                return await self._cerrar(res, inicio, bitacora, email)

            # Lo que un area reenvia se radica a nombre de quien escribio. Sin
            # nadie de afuera en el hilo no hay de quien sea la PQRD.
            nombre_de = email.from_address.name if email.from_address else ""
            correo_de = email.from_address.address if email.from_address else ""
            if (es_buzon_de_positiva(correo_de)
                    and not tiene_remitente_externo_identificable(
                        nombre_de, correo_de, cuerpo)):
                res.accion = "manual"
                res.motivo_manual = MOTIVO_REMITENTE_NO_IDENTIFICABLE
                res.avisos.append(
                    f"lo escribe {correo_de} y el hilo no trae ningun remitente "
                    f"externo: no se radica")
                await self._a_revision_manual(
                    email, res.motivo_manual, res.avisos, res)
                return await self._cerrar(res, inicio, bitacora, email)

            with res.cronometrar("descarga_adjuntos"):
                adjuntos = await self._descargar_adjuntos(email, bitacora)
            nombres = [n for n, _ in adjuntos]

            # Antes de convertir y antes del modelo. Un correo con decenas de
            # paginas escaneadas no se atiende mejor por pasarlo entero: satura
            # el reconocimiento de texto y le da al agente mas informacion de la
            # que puede sopesar para decidir un dato.
            with res.cronometrar("volumen"):
                cuanto = volumen.medir(adjuntos)
            if cuanto.se_pasa:
                res.accion = "manual"
                res.motivo_manual = volumen.MOTIVO
                res.avisos.append(
                    f"el correo se pasa de lo que se procesa: "
                    f"{cuanto.detalle()} ({cuanto.resumen()})")
                await self._a_revision_manual(
                    email, res.motivo_manual, res.avisos, res)
                return await self._cerrar(res, inicio, bitacora, email)

            # Capas de deduplicacion que necesitan los adjuntos, todavia antes
            # de llamar al modelo.
            with res.cronometrar("deduplicacion"):
                repetido = await self.duplicados.comprobar_contenido(email, nombres)
            if repetido.motivo and not repetido.es_duplicado:
                res.avisos.append(f"deduplicacion degradada: {repetido.motivo}")
            if repetido.es_duplicado:
                res.accion = "duplicado"
                res.razon_del_duplicado = repetido.motivo
                res.avisos.append(repetido.motivo)
                await self._archivar_duplicado(email, res.avisos)
                return await self._cerrar(res, inicio, bitacora, email)

            # Aqui esta el OCR: es la etapa que puede tardar minutos con un
            # escaneo de muchas paginas.
            with res.cronometrar("conversion_a_markdown"):
                # Convertir y aplicar OCR es sincrono y tarda segundos. Va a un
                # hilo por lo mismo que el modelo: sin eso, un escaneo largo
                # detiene a todo el lote.
                ws = await asyncio.to_thread(
                    materializar,
                    id_correo=email.id,
                    asunto=email.subject or "",
                    cuerpo_limpio=cuerpo,
                    remitente_nombre=(email.from_address.name if email.from_address else ""),
                    remitente_correo=(email.from_address.address if email.from_address else ""),
                    adjuntos=adjuntos,
                    base=self.base_workspace,
                    temporal=self.base_workspace is None,
                )
            try:
                res.avisos.extend(ws.manifiesto.avisos)
                for entrada in ws.manifiesto.fallidos:
                    bitacora.registrar(
                        "CONVERSION", f"{entrada.nombre_original}: {entrada.detalle}",
                        cuerpo=entrada.detalle,
                    )

                texto_adjuntos = "\n\n".join(
                    ws.leer(Path(e.ruta_md).stem)
                    for e in ws.manifiesto.convertidos
                )

                with res.cronometrar("saneamiento"):
                    sospechas = detectar(f"{cuerpo}\n{texto_adjuntos}")
                altas = [s for s in sospechas if s.severidad is Severidad.ALTA]
                if altas:
                    res.avisos.append(
                        "inyeccion detectada: "
                        + ", ".join(sorted({s.categoria for s in altas}))
                    )

                ilegibles = [
                    e.nombre_original for e in ws.manifiesto.fallidos
                ]
                with res.cronometrar("extraccion_determinista"):
                    determinista = extraer_todo(
                        asunto=email.subject or "",
                        cuerpo=cuerpo,
                        texto_adjuntos=texto_adjuntos,
                        adjuntos_ilegibles=ilegibles,
                        from_name=(email.from_address.name if email.from_address else ""),
                        from_address=(email.from_address.address if email.from_address else ""),
                        sender_name=(email.sender.name if email.sender else ""),
                        nombres_hilo=self._nombres_hilo(email),
                        cuerpo_actual=cuerpo,
                    )

                ctx = Contexto(
                    workspace=ws,
                    consultar_persona=self._consultar_persona,
                    # Lo que ya esta resuelto viaja al revisor aunque el agente
                    # no lo reporte: forma parte de lo que se va a enviar.
                    resueltos={c: h.valor
                               for c, h in determinista.hallazgos.items()},
                    revisar=self._revisor(ws),
                )
                with res.cronometrar("agente"):
                    resolucion = await self._resolver_con_reintento(
                        ctx, determinista, bitacora, res)
                res.tokens_entrada = resolucion.tokens_entrada
                res.tokens_salida = resolucion.tokens_salida
                res.turnos_del_agente = resolucion.iteraciones
                res.explicacion_del_agente = resolucion.explicacion
                res.avisos.extend(resolucion.descartados)
                res.avisos.extend(resolucion.corregidos)
                res.avisos.extend(resolucion.avisos)
                if resolucion.agotado:
                    # Con el detalle de en que se le fueron: sin eso el aviso
                    # dice que se acabaron las vueltas y no por que.
                    gasto = ", ".join(
                        f"{h} x{n}" for h, n
                        in sorted(resolucion.herramientas_usadas.items(),
                                  key=lambda kv: -kv[1])
                    ) or "ninguna herramienta"
                    repetidas = (
                        f", {resolucion.llamadas_repetidas} repetidas"
                        if resolucion.llamadas_repetidas else ""
                    )
                    res.avisos.append(
                        f"el agente agoto las vueltas sin cerrar: "
                        f"{resolucion.iteraciones} vueltas, {gasto}{repetidas}")

                hallazgos = combinar(determinista, resolucion)
                res.atribucion = atribucion(determinista, resolucion)

                # La oficina sale de la tabla una vez se conoce el tipo de
                # solicitud, que solo se resuelve despues del agente.
                if "oficina" not in hallazgos and "tipo_solicitud" in hallazgos:
                    from pipeline.extraccion import extraer_oficina

                    h_of, _ = extraer_oficina(hallazgos["tipo_solicitud"].valor)
                    if h_of is not None:
                        hallazgos["oficina"] = h_of
                        res.atribucion["oficina"] = Origen.ALGORITMO

                # Cuando el remitente ES el afectado, los datos se copian aqui
                # y no en el radicador, para que la compuerta los valide.
                self._heredar_datos_del_afectado(hallazgos, res)

                # Va antes de la compuerta para que lo del registro pase por las
                # mismas comprobaciones que lo del correo.
                contradicciones: List[str] = []
                with res.cronometrar("registro_de_personas"):
                    for campo, hallazgo in rellenar_desde_el_registro(
                        hallazgos, self._consultar_persona_del_afectado,
                        res.avisos, contradicciones,
                        texto=f"{cuerpo}\n{texto_adjuntos}",
                        principal=f"{email.subject or ''}\n{cuerpo}",
                    ).items():
                        hallazgos[campo] = hallazgo
                        res.atribucion[campo] = Origen.REGISTRO

                # El documento y el nombre describen a dos personas distintas
                # segun el registro. Radicar escribe una identidad que no
                # existe: o la PQRD queda a nombre de quien no la puso, o queda
                # con la cedula de otro. Lo decide una persona.
                if contradicciones:
                    res.accion = "manual"
                    res.motivo_manual = "DOCUMENTO_DE_OTRA_PERSONA"
                    res.avisos.extend(contradicciones)
                    self._registrar_lo_no_radicado(
                        email, res.motivo_manual, hallazgos, contradicciones)
                    await self._a_revision_manual(
                        email, res.motivo_manual, res.avisos, res)
                    return await self._cerrar(res, inicio, bitacora, email)

                with res.cronometrar("compuerta"):
                    dictamen = compuerta.validar(
                        hallazgos, ctx.catalogos,
                        motivos_no_resueltos={
                            campo: nr.motivo
                            for campo, nr in resolucion.sin_resolver.items()
                        },
                    )
                if dictamen.veredicto is Veredicto.CORREGIDO:
                    hallazgos = compuerta.aplicar(hallazgos, dictamen)
                    res.avisos.extend(compuerta.avisos(dictamen))
                res.hallazgos = hallazgos

                if dictamen.veredicto is Veredicto.RECHAZADO:
                    res.accion = "manual"
                    res.motivo_manual = dictamen.motivo_manual
                    res.avisos.extend(compuerta.avisos(dictamen))
                    self._registrar_lo_no_radicado(
                        email, res.motivo_manual, hallazgos,
                        [f"{r.codigo}: {r.campo}: {r.detalle}"
                         for r in dictamen.bloqueantes])
                    await self._a_revision_manual(email, res.motivo_manual, res.avisos, res)
                    return await self._cerrar(res, inicio, bitacora, email)

                if not bitacora.puede_radicar:
                    res.accion = "manual"
                    res.motivo_manual = bitacora.motivo_manual()
                    self._registrar_lo_no_radicado(
                        email, res.motivo_manual, hallazgos, bitacora.avisos())
                    await self._a_revision_manual(email, res.motivo_manual, res.avisos, res)
                    return await self._cerrar(res, inicio, bitacora, email)

                with res.cronometrar("radicacion"):
                    radicado = await self._radicar(
                        email, hallazgos, bitacora,
                        archivos=adjuntos, cuerpo_limpio=cuerpo, avisos=res.avisos,
                    )
                if radicado:
                    res.accion = "radicado"
                    res.radicado = radicado
                    # Guardar el hash alimenta la capa de dedup por contenido.
                    with res.cronometrar("cierre_outlook"):
                        fallo = await self.duplicados.registrar_radicado(email, nombres)
                        if fallo:
                            res.avisos.append(f"deduplicacion degradada: {fallo}")
                        await self._mover_a_exitosa(email, radicado, res.avisos)
                else:
                    res.accion = "manual"
                    # Se nombra el paso: si la radicacion no dejo un fatal con
                    # motivo, vale la degradacion que ocurrio radicando, y solo
                    # esa. Una de otro paso no explica este desenlace.
                    motivo = bitacora.motivo_manual("RADICACION")
                    if not motivo:
                        # La radicacion no dejo numero y la bitacora no dice por
                        # que. Sin esta linea el correo sale con
                        # ERROR_INESPERADO y ni el informe ni el aviso llevan
                        # nada que permita saber que paso.
                        res.avisos.append(
                            "la radicacion termino sin numero y sin registrar "
                            "la causa: no hay con que diagnosticarlo, revisar "
                            f"la respuesta de {type(self.radicador).__name__}")
                    res.motivo_manual = motivo or "ERROR_INESPERADO"
                    await self._a_revision_manual(email, res.motivo_manual, res.avisos, res)
            finally:
                ws.cerrar()

        except FalloDelModelo as e:
            res.accion = "manual"
            res.motivo_manual = e.motivo
            await self._a_revision_manual(email, res.motivo_manual, res.avisos, res)
        except Exception as e:
            fallo = bitacora.registrar("PIPELINE", str(e), excepcion=e)
            res.accion = "manual"
            res.motivo_manual = fallo.motivo_manual or "ERROR_INESPERADO"
            await self._a_revision_manual(email, res.motivo_manual, res.avisos, res)

        return await self._cerrar(res, inicio, bitacora, email)

    # ── Cierre y telemetria ──

    async def _cerrar(
        self, res: Resultado, inicio: float, bitacora: Bitacora,
        email: EmailMessage,
    ) -> Resultado:
        res.duracion_ms = int((time.time() - inicio) * 1000)
        res.avisos.extend(bitacora.avisos())
        # Punto unico de salida: el aviso se manda pase lo que pase con el
        # correo, que es lo que evita que quien revisa tenga que ir a los logs.
        await self._avisar(res, email)
        if self.ejecucion is not None:
            self.ejecucion.registrar(RegistroCorreo(
                id_correo=res.email_id,
                asunto=res.asunto,
                accion=res.accion,
                radicado=res.radicado,
                motivo_manual=res.motivo_manual,
                area_destino=res.area_destino,
                razon_del_reenvio=res.razon_del_reenvio,
                explicacion=res.explicacion_del_agente,
                duracion_ms=res.duracion_ms,
                etapas_ms=dict(res.etapas_ms),
                atribucion=dict(res.atribucion),
                avisos=list(res.avisos),
            ))
            self.ejecucion.contador.registrar(
                "agente", getattr(self.modelo, "nombre", "?"),
                res.tokens_entrada, res.tokens_salida,
                turnos=res.turnos_del_agente,
            )
        return res

    @staticmethod
    def _registrar_lo_no_radicado(
        email: EmailMessage, motivo: str,
        hallazgos: Dict[str, Hallazgo], rechazos: List[str],
    ) -> None:
        """Deja en el log que campos se leyeron y por que no se enviaron a SGDEA."""
        del_correo = datos_del_correo(email)
        emitir(
            f"[{del_correo['correo']}] no se radica: {motivo}",
            severidad=AVISO,
            solo_json=("correo", "motivo_manual", "internet_message_id"),
            en_su_linea=("rechazos", "remitente_correo", "asunto", "recibido",
                         "campos_faltantes", "campos_leidos"),
            motivo_manual=motivo,
            rechazos=rechazos,
            **del_correo,
            campos_faltantes=[c for c in compuerta.OBLIGATORIOS
                              if c not in hallazgos
                              or hallazgos[c].valor in (None, "", [])],
            campos_leidos={c: h.valor for c, h in sorted(hallazgos.items())},
        )

    # ── Adaptadores. Sin servicio configurado no hacen nada. ──

    async def _descargar_adjuntos(
        self, email: EmailMessage, bitacora: Bitacora
    ) -> List[Tuple[str, bytes]]:
        if self.outlook is None or not email.has_attachments:
            return []
        import base64

        salida: List[Tuple[str, bytes]] = []
        try:
            lista = await self.outlook.get_attachments(email.id)
        except Exception as e:
            bitacora.registrar("ADJUNTOS", "no se pudo listar", excepcion=e)
            return []
        for a in lista:
            nombre = a.get("name") or "adjunto"
            if a.get("isInline") and a.get("size", 0) < 20_000:
                continue
            try:
                b64 = await self.outlook.download_attachment(email.id, a["id"])
                if b64:
                    salida.append((nombre, base64.b64decode(b64)))
            except Exception as e:
                bitacora.registrar("ADJUNTOS", f"fallo {nombre}", excepcion=e)
        return salida

    def _nombres_hilo(self, email: EmailMessage) -> List[Tuple[str, str]]:
        pares = []
        if email.from_address:
            pares.append((email.from_address.name, email.from_address.address))
        if email.sender:
            pares.append((email.sender.name, email.sender.address))
        return pares

    @staticmethod
    def _heredar_datos_del_afectado(
        hallazgos: Dict[str, Hallazgo], res: Resultado
    ) -> None:
        """
        Copia los datos del afectado al remitente cuando son la misma persona.

        Solo con `tipo_remitente == 3`. Para un familiar o un tercero, copiar
        registra a otra persona con la cedula del trabajador; eso lo rechaza la
        compuerta y aqui no se hace.
        """
        from pipeline.radicador import TIPO_REMITENTE_ES_EL_AFECTADO

        tipo = hallazgos.get("tipo_remitente")
        if tipo is None or tipo.valor != TIPO_REMITENTE_ES_EL_AFECTADO:
            return
        for campo in ("numero_documento", "tipo_documento"):
            origen = hallazgos.get(f"{campo}_afectado")
            if origen is None or f"{campo}_remitente" in hallazgos:
                continue
            hallazgos[f"{campo}_remitente"] = Hallazgo(
                campo=f"{campo}_remitente",
                valor=origen.valor,
                evidencia=f"el remitente es el afectado: se toma su {campo}",
                confianza=origen.confianza,
                metodo="remitente_es_el_afectado",
            )
            res.atribucion[f"{campo}_remitente"] = Origen.ALGORITMO

    def _revisor(self, ws: Any):
        """
        La funcion con la que el agente delega la revision, o None.

        Se construye aqui porque es donde estan el modelo y el workspace. El
        revisor recibe un contexto propio con SOLO las herramientas de lectura:
        no reporta hallazgos ni consulta el registro, y no puede pedirse una
        revision a si mismo.
        """
        from pipeline import revisor as revisor_mod

        esquemas = [e for e in ESQUEMAS_HERRAMIENTAS
                    if e["name"] in revisor_mod.HERRAMIENTAS_DEL_REVISOR]

        def revisar(valores: Dict[str, Any]):
            propio = Contexto(workspace=ws)
            return revisor_mod.revisar(self.modelo, propio, valores, esquemas)

        return revisar

    def _sin_tiempo(self) -> bool:
        """Si a la corrida ya no le queda presupuesto para seguir con este correo."""
        return self.presupuesto is not None and self.presupuesto.agotado()

    async def _resolver_con_reintento(self, ctx, determinista, bitacora, res):
        """
        Llama al agente, reintentando los fallos que la taxonomia da por
        pasajeros.

        Sin esto, un corte de red de dos minutos manda a revision manual todos
        los correos del lote: el job corre cada media hora y nadie los vuelve a
        mirar. La politica es la misma que usa el radicador, para que un fallo
        de red no se trate distinto segun a quien se le pidio algo.
        """
        intento = 0
        while True:
            intento += 1
            try:
                # `resolver` es sincrono y pasa minutos esperando al modelo.
                # Llamado directo bloquearia el bucle de eventos, y los demas
                # correos del lote esperarian su turno en vez de avanzar.
                return await asyncio.to_thread(
                    resolver, self.modelo, ctx, determinista)
            except Exception as e:
                clase = clasificar(cuerpo=str(e), excepcion=e)
                if debe_reintentar(clase, intento) and not self._sin_tiempo():
                    espera = espera_para(clase, intento)
                    if self.presupuesto is not None:
                        espera = self.presupuesto.recortar(espera)
                    res.avisos.append(
                        f"el modelo fallo con {clase.codigo} y se reintenta: "
                        f"intento {intento}, espera {espera:.0f}s"
                    )
                    if espera:
                        await self.dormir(espera)
                    continue
                if self._sin_tiempo():
                    # El correo queda reclamado y sin radicar. Aplazarlo lo
                    # devuelve a la bandeja con su cuenta: la corrida siguiente
                    # lo toma con el presupuesto entero por delante.
                    bitacora.registrar(
                        "MODELO", f"sin tiempo de corrida: {e}", excepcion=e,
                        intento=intento)
                    res.avisos.append(
                        f"se agoto el tiempo de la corrida tras {intento} "
                        f"intento(s): el correo se aplaza")
                    raise FalloDelModelo(
                        "TIEMPO_DE_CORRIDA_AGOTADO", e) from e
                fallo = bitacora.registrar("MODELO", str(e), excepcion=e,
                                           intento=intento)
                raise FalloDelModelo(
                    fallo.motivo_manual or "MODELO_NO_RESPONDIO", e) from e

    def _consultar_persona_del_afectado(
        self, documento: str, tipo: Any = None
    ) -> Optional[Dict[str, Any]]:
        """
        Como `_consultar_persona`, pero traduce cualquier fallo al tipo que el
        relleno sabe manejar.

        Aqui la consulta es un extra: si el registro no responde, el correo se
        radica igual con lo que se leyo de el. La herramienta del agente, en
        cambio, necesita que el fallo suba tal cual para poder distinguirlo de
        una persona inexistente.
        """
        try:
            return self._consultar_persona(documento, tipo)
        except ConsultaDePersonaFallo:
            raise
        except Exception as e:
            raise ConsultaDePersonaFallo(f"{type(e).__name__}: {e}") from e

    def _consultar_persona(
        self, documento: str, tipo: Any = None
    ) -> Optional[Dict[str, Any]]:
        """
        Consulta la persona por documento. Devuelve None si no hay servicio.

        Un fallo de consulta se registra en la bitacora y se propaga, para que
        la herramienta lo reporte como error y no como persona inexistente.
        """
        if self.sgdea is None:
            return None
        try:
            datos = (self.sgdea.consultar(documento, tipo) if tipo not in (None, "")
                     else self.sgdea.consultar(documento))
        except Exception as e:
            _bitacora().registrar(
                "SGDEA_CONSULTA", f"no se pudo consultar {documento}", excepcion=e)
            raise
        # Un cliente que captura la excepcion deja el motivo en `ultimo_error`.
        fallo = getattr(self.sgdea, "ultimo_error", "")
        if fallo:
            _bitacora().registrar(
                "SGDEA_CONSULTA", f"no se pudo consultar {documento}: {fallo}")
            raise RuntimeError(fallo)
        return datos

    async def _radicado_por_otra_corrida(
        self, email: EmailMessage, avisos: Optional[List[str]] = None,
    ) -> str:
        """
        El radicado que otra corrida le puso mientras se procesaba, o vacio.

        Si no se puede releer el correo NO se da por radicado: se sigue y se
        anota. Un fallo de lectura no es evidencia de nada, y tratarlo como si
        lo fuera dejaria el caso sin radicar sin que nadie lo sepa.
        """
        if self.outlook is None:
            return ""
        leer = getattr(self.outlook, "get_email_by_id", None)
        if leer is None:
            return ""
        try:
            fresco = await leer(email.id)
        except Exception as e:
            if avisos is not None:
                avisos.append(
                    f"no se pudo releer el correo antes de radicar "
                    f"({type(e).__name__}): se radica igual, con riesgo de "
                    f"duplicado si otra corrida lo hizo a la vez")
            return ""
        return reclamo.otro_lo_radico(getattr(fresco, "categories", None))

    async def _radicar(
        self,
        email: EmailMessage,
        hallazgos: Dict[str, Hallazgo],
        bitacora: Bitacora,
        archivos: Optional[List[Tuple[str, bytes]]] = None,
        cuerpo_limpio: str = "",
        avisos: Optional[List[str]] = None,
    ) -> str:
        if self.radicador is None:
            return "DRY-RUN"

        # Ultima comprobacion antes de lo unico que no se puede deshacer. El
        # correo se reclamo hace minutos —conversion, OCR y modelo— y en ese
        # rato otra corrida pudo haberlo radicado. Releer sus etiquetas cuesta
        # una peticion y evita el segundo radicado.
        ya = await self._radicado_por_otra_corrida(email, avisos)
        if ya:
            if avisos is not None:
                avisos.append(
                    f"otra corrida lo radico mientras se procesaba ({ya}): no "
                    f"se radica de nuevo")
            return ya

        self.radicador.bitacora = bitacora
        try:
            return await self.radicador.radicar(
                email, hallazgos, archivos or [], cuerpo_limpio
            )
        except Exception as e:
            bitacora.registrar("RADICACION", str(e), excepcion=e,
                               cuerpo=getattr(e, "cuerpo", "") or str(e))
            return ""
        finally:
            # Los campos enviados con valor por defecto van al informe.
            if avisos is not None:
                for s in getattr(self.radicador, "suposiciones", []):
                    avisos.append(f"por defecto: {s}")

    async def _archivar_duplicado(
        self, email: EmailMessage, avisos: Optional[List[str]] = None,
    ) -> None:
        """
        Saca de la bandeja el correo que ya estaba radicado.

        Un duplicado que se queda en la bandeja se vuelve a tomar cada media
        hora: ocupa un cupo del lote, se vuelve a detectar y se vuelve a
        avisar, indefinidamente. No lleva marca de desenlace propia porque la
        que lo identifica ya esta puesta —es la del radicado, que es
        justamente por la que se detecto—, asi que solo se retiran las de
        proceso y se mueve.
        """
        await self._archivar(
            email, "", settings.OUTLOOK_FOLDER_DUPLICADOS, avisos)

    async def _archivar(
        self,
        email: EmailMessage,
        etiqueta: str,
        carpeta: str,
        avisos: Optional[List[str]] = None,
    ) -> None:
        """
        Etiqueta el correo y lo saca de la bandeja.

        El fallo no se propaga: la radicacion ya ocurrio y perderla por no
        poder mover un correo seria peor. Pero tampoco se traga, porque la
        etiqueta es de lo que dependen las capas 1 y 3 de deduplicacion: si no
        se pone, el correo se vuelve a procesar en la siguiente corrida y se
        radica dos veces. Un fallo aqui es una radicacion duplicada mañana.
        """
        if self.outlook is None:
            return
        # El desenlace ya consta en su propia marca: las de proceso se retiran
        # para que la siguiente corrida no lea trabajo a medias donde no lo hay.
        await self._marcar(
            email, etiqueta, avisos, quitar_de_proceso=True,
            consecuencia="el correo se volvera a procesar y puede radicarse "
                         "dos veces")

        mover = getattr(self.outlook, "move_email", None)
        if mover is None:
            return
        try:
            nuevo_id = await mover(email.id, carpeta)
        except Exception as e:
            if avisos is not None:
                avisos.append(
                    f"move_email fallo ({type(e).__name__}: {e}): el correo se "
                    f"queda en la bandeja de entrada")
            return
        # Al mover, Graph le da otro identificador al mensaje y el anterior deja
        # de existir. Sin quedarse con el nuevo, responder al correo da 404.
        if nuevo_id:
            email.id = nuevo_id

    async def _avisar(self, res: Resultado, email: EmailMessage) -> None:
        """
        Remite el resultado del procesamiento al buzon interno de revision.

        El aviso va SIEMPRE a `CORREO_DE_AVISOS` y NUNCA a quien escribio. Lo
        lee personal de la entidad para entender el desenlace sin abrir los
        registros de la ejecucion; el ciudadano, la empresa o el juzgado que
        remitieron la comunicacion no reciben nada de aqui. Sin destino
        configurado no se envia: el desenlace consta igual en la categoria del
        correo y en el informe.

        Un fallo al avisar NO cambia el desenlace: la radicacion ya ocurrio y no
        se deshace, y un correo que se fue a revision manual sigue estando ahi.
        Pero consta, porque sin el aviso alguien va a preguntar por que.

        Un correo que tiene otra corrida encima no se avisa: es esa corrida la
        que lo esta trabajando y la que va a dejar el aviso. Dos avisos sobre el
        mismo correo se leen como dos procesamientos.

        Tampoco se avisa el que no se pudo reclamar. Ese correo quedo intacto y
        no se hizo nada con el: un aviso lo presentaria como un desenlace, y la
        corrida siguiente lo va a tomar y avisar lo que corresponda.
        """
        if self.outlook is None or res.accion in ("en_proceso", "sin_reclamar"):
            return
        destino = (settings.CORREO_DE_AVISOS or "").strip()
        if not destino:
            return
        remitir = getattr(self.outlook, "forward_email", None)
        if remitir is None:
            return
        try:
            await remitir(email.id, destino, notificacion.redactar(
                accion=res.accion,
                radicado=res.radicado,
                motivo=res.motivo_manual,
                detalle=self._detalle_del_aviso(res),
                oficina=self._nombre_de_la_oficina(res),
            ))
        except Exception as e:
            res.avisos.append(
                f"no se pudo remitir el aviso a {destino} "
                f"({type(e).__name__}: {e}): quien lo revise no vera por que "
                f"quedo asi")

    @staticmethod
    def _detalle_del_aviso(res: Resultado) -> str:
        """
        La explicacion concreta de ESTE correo, si la hay.

        Manda la frase del agente: es quien leyo la comunicacion y puede decir
        que dato falto y por que no estaba. El motivo del catalogo es una
        etiqueta fija y no distingue un correo de otro.

        Cuando el agente no habla —no se le llamo, o el fallo fue de
        infraestructura— se cae a la lista de campos sin resolver, que al menos
        dice cuales fueron.
        """
        if res.accion == "reenviado":
            for aviso in res.avisos:
                if aviso.startswith("reenviado a "):
                    return aviso[0].upper() + aviso[1:]
            return ""
        if not explica_el_desenlace(res.accion, res.motivo_manual):
            # La frase del agente habla de un dato que falto, y solo explica el
            # correo que se cerro por eso. Con un desenlace de infraestructura
            # ese dato no es la causa, y en uno radicado no impidio nada:
            # mandarla presenta como problema un correo que salio bien.
            return ""
        if res.explicacion_del_agente:
            return res.explicacion_del_agente
        faltantes = sorted(
            campo for campo, origen in res.atribucion.items()
            if origen is Origen.NO_RESUELTO
        )
        if faltantes:
            return "Datos que no se pudieron obtener: " + ", ".join(faltantes) + "."
        return ""

    @staticmethod
    def _nombre_de_la_oficina(res: Resultado) -> str:
        h = res.hallazgos.get("oficina")
        if h is None:
            return ""
        from business_rules import get_office_name

        try:
            return get_office_name(int(h.valor)) or ""
        except (TypeError, ValueError):
            return ""

    async def _reenviar(
        self, email: EmailMessage, ruta, avisos: Optional[List[str]] = None,
    ) -> None:
        """
        Manda el correo al area que le corresponde y lo saca de la bandeja.

        Si el reenvio falla NO se archiva: sacarlo de la bandeja sin haberlo
        mandado a ninguna parte lo pierde, y nadie se entera hasta que el
        ciudadano reclama. Quedandose en la bandeja, la siguiente corrida lo
        vuelve a intentar.
        """
        if self.outlook is None:
            return
        reenviar = getattr(self.outlook, "forward_email", None)
        if reenviar is None:
            if avisos is not None:
                avisos.append(
                    "no se reenvio: el servicio de correo no expone "
                    "forward_email; el correo se queda en la bandeja")
            return
        try:
            await reenviar(
                email.id, ruta.correo_destino,
                notificacion.encabezado_de_reenvio(
                    ruta.nombre_del_area, ruta.evidencia),
            )
        except Exception as e:
            if avisos is not None:
                avisos.append(
                    f"el reenvio a {ruta.correo_destino} fallo "
                    f"({type(e).__name__}: {e}): el correo se queda en la "
                    f"bandeja y se reintenta en la siguiente corrida")
            return
        await self._archivar(
            email, f"REENVIADO:{ruta.destino.value}",
            settings.OUTLOOK_FOLDER_REENVIADO_OTRA_AREA, avisos)

    async def _a_revision_manual(
        self, email: EmailMessage, motivo: str,
        avisos: Optional[List[str]] = None,
        res: Optional[Resultado] = None,
    ) -> None:
        """
        Cierra el correo hacia una persona, salvo que el fallo sea de los que
        se reparan solos.

        Todos los caminos a revision manual pasan por aqui, que es lo que hace
        que la decision de aplazar no dependa de acordarse de aplicarla en cada
        uno.
        """
        aplazar, intento, explicacion = reintentos.decidir(
            motivo, getattr(email, "categories", None))
        if explicacion and avisos is not None:
            avisos.append(explicacion)

        if aplazar:
            # No se mueve de carpeta: quedarse en la bandeja ES lo que hace que
            # la siguiente corrida lo vuelva a tomar.
            if res is not None:
                res.accion = "aplazado"
            # Se retira la marca de proceso: esta corrida ya termino con el
            # correo, y lo que la siguiente necesita saber es cuantas veces se
            # aplazo, no que alguien lo tenia tomado.
            marca = reintentos.etiqueta_de_intento(intento)
            await self._marcar(
                email, marca, avisos, quitar_de_proceso=True,
                consecuencia="sin la cuenta de aplazamientos, el correo se "
                             "cierra a revision manual en esta corrida")
            # Sin buzon no hay marcas que escribir ni carpetas a las que mover:
            # la comprobacion no aplica.
            if self.outlook is None or reintentos.intentos_previos(
                    getattr(email, "categories", None)) >= intento:
                return
            # La marca no quedo escrita. Sin ella el correo vuelve sin cuenta y
            # puede dar vueltas sin llegar nunca a una persona: se cierra ahora,
            # que es el desenlace que si consta en la carpeta.
            if avisos is not None:
                avisos.append(
                    f"no quedo escrita la marca '{marca}': el correo se cierra "
                    f"a revision manual para que no se reintente sin cuenta")
            if res is not None:
                res.accion = "manual"
            await self._archivar(
                email, f"REVISION-MANUAL:{motivo}",
                settings.OUTLOOK_FOLDER_REVISAR_MANUAL, avisos)
            return

        await self._archivar(
            email, f"REVISION-MANUAL:{motivo}",
            settings.OUTLOOK_FOLDER_REVISAR_MANUAL, avisos)

    async def _marcar(
        self,
        email: EmailMessage,
        marca: str,
        avisos: Optional[List[str]] = None,
        quitar_de_proceso: bool = False,
        obligatoria: bool = False,
        consecuencia: str = "la siguiente corrida no vera el estado en el que quedo",
    ) -> None:
        """
        Deja una marca en el correo sin mover el correo ni borrar las demas.

        `consecuencia` es lo que pasa si la marca no se escribe, y cambia segun
        cual sea: perder la del radicado permite una segunda radicacion, y
        perder la del aplazamiento solo pierde la cuenta.

        Con `obligatoria` ningun fallo deja continuar, y son dos distintos. Que
        otra corrida se haya adelantado sale como `OtraCorridaLoTomo`: el correo
        tiene dueño y es esa corrida la que lo trabaja. Que la escritura falle
        sale como `ReclamoNoEscrito`: no lo tiene nadie, y seguir permitiria que
        la corrida siguiente lo tomara tambien y las dos lo radicaran.

        En las demas marcas el desenlace ya ocurrio y perderlo por no poder
        escribir una categoria seria peor, asi que el fallo consta y el correo
        sigue: quedaria sin contador, se reintentaria sin llevar la cuenta y
        podria dar vueltas, pero eso no duplica nada.
        """
        if self.outlook is None:
            return
        metodo = getattr(self.outlook, "marcar", None)
        try:
            if metodo is not None:
                quedaron = await metodo(email.id, marca, quitar_de_proceso)
                if quedaron is not None:
                    email.categories = list(quedaron)
                return
            # Servicio sin `marcar`: se etiqueta como se pueda y consta que las
            # demas marcas del correo no sobreviven.
            antiguo = getattr(self.outlook, "etiquetar_correo", None)
            if antiguo is None:
                return
            await antiguo(email.id, marca)
            email.categories = [marca]
        except OtraCorridaLoTomo:
            if obligatoria:
                raise
            if avisos is not None:
                avisos.append(
                    f"otra corrida modifico el correo al escribir '{marca}': "
                    f"la marca no se puso")
        except Exception as e:
            if obligatoria:
                raise ReclamoNoEscrito(
                    f"no se pudo reclamar el correo con '{marca}' "
                    f"({type(e).__name__}: {e}): queda sin dueño y no se "
                    f"procesa, para que no lo tomen dos corridas"
                ) from e
            if avisos is not None:
                avisos.append(
                    f"marcar el correo con '{marca}' fallo "
                    f"({type(e).__name__}: {e}): {consecuencia}")

    async def _mover_a_exitosa(
        self, email: EmailMessage, radicado: str,
        avisos: Optional[List[str]] = None,
    ) -> None:
        # La etiqueta es lo que despues delata que el caso ya se atendio. El
        # formato del identificador lo decide SGDEA, no nosotros: si cambia y
        # se escribe tal cual, deja de reconocerse y la deduplicacion muere
        # sin que nadie se entere.
        etiqueta, anomalia = etiqueta_para(radicado)
        if anomalia and avisos is not None:
            avisos.append(f"etiqueta de radicado: {anomalia}")
        if not etiqueta:
            return
        await self._archivar(
            email, etiqueta,
            settings.OUTLOOK_FOLDER_RADICACION_EXITOSA, avisos)
