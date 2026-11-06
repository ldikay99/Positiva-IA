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
from pipeline.correccion import campos_a_corregir, extraccion_para_corregir
from pipeline.duplicados import Detector
from pipeline.enrutamiento import (
    MOTIVO_REMITENTE_EXCLUIDO,
    MOTIVO_REMITENTE_NO_IDENTIFICABLE,
    Destino,
    decidir,
)
from pipeline.errores import (
    DESCONOCIDO,
    POR_CODIGO,
    Bitacora,
    FalloDelModelo,
    clasificar,
    debe_reintentar,
    espera_para,
)
from models.enums import TipoDocumento
from pipeline.extraccion import (
    Hallazgo,
    extraer_todo,
    motivo_por_el_que_no_es_un_nombre,
)
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
from servidor_mcp.herramientas import CATALOGOS, Contexto
from utils.generic_filter import (
    es_buzon_de_positiva,
    tiene_remitente_externo_identificable,
)
from utils.html_cleaner import clean_html_content
from utils.radicado import etiqueta_para


# Lo que el revisor comprueba siempre antes de radicar. Son los campos que, mal
# puestos, radican a nombre de otra persona.
CAMPOS_DE_IDENTIDAD = (
    "nombre_afectado", "numero_documento_afectado", "tipo_documento_afectado",
    "nombre_remitente", "numero_documento_remitente", "tipo_documento_remitente",
    "tipo_remitente",
)
# Lo que viaja al revisor. Ademas de la identidad, el tipo de solicitud: antes
# lo revisaba porque el agente pedia la revision al cerrar, y sin ella el
# corpus perdio cinco aciertos de tipo_solicitud. Va en la misma llamada.
CAMPOS_REVISADOS = CAMPOS_DE_IDENTIDAD + ("tipo_solicitud",)

# Cuanto texto se le muestra al revisor alrededor de cada aparicion.
ANCHO_DEL_EXTRACTO = 90
EXTRACTOS_POR_CAMPO = 2


def extractos_del_valor(valor: Any, texto_plegado: str,
                        maximo: int = EXTRACTOS_POR_CAMPO,
                        ancho: int = ANCHO_DEL_EXTRACTO) -> List[str]:
    """
    Donde aparece el valor en el texto del correo, ya plegado: las cifras con
    sus separadores y el nombre con sus palabras en orden.
    """
    import re

    crudo = str(valor or "")
    digitos = compuerta._digitos_ascii(crudo).lstrip("0")
    if digitos and len(digitos) >= 5 and digitos == re.sub(r"\D", "", crudo).lstrip("0"):
        patron = re.compile(r"(?<!\d)0*" + r"[.\s,'\-]?".join(digitos) + r"(?!\d)")
    else:
        palabras = [re.escape(p) for p in re.findall(r"[a-z]+", compuerta._normaliza(crudo))
                    if len(p) > 1]
        if not palabras:
            return []
        patron = re.compile(r"\b" + r"\W+(?:\w+\W+)?".join(palabras) + r"\b")
    salida = []
    for m in patron.finditer(texto_plegado or ""):
        salida.append("..." + texto_plegado[max(0, m.start() - ancho): m.end() + ancho // 2]
                      + "...")
        if len(salida) >= maximo:
            break
    return salida


# Del lado del remitente el revisor solo aconseja, salvo en el nombre que no lo
# es. Quien es "el remitente" en un hilo reenviado, en un buzon de Positiva o
# cuando el afectado escribe desde el correo de otro es una convencion del
# negocio que la compuerta ya aplica; en el corpus el revisor la contradecia
# (cambiaba a quien firma por el nombre del buzon y el tipo 3 por 1 o 2) y
# empeoraba tipo_remitente y el documento del remitente sin arreglar nada.
CAMPOS_EN_LOS_QUE_EL_REVISOR_SOLO_AVISA = frozenset({
    "tipo_remitente", "numero_documento_remitente", "tipo_documento_remitente",
})
# Campos en los que el revisor puede proponer otro valor (CAMBIAR) pero nunca
# dejarlos vacios: el tipo de solicitud es obligatorio y "no respaldado" ahi
# significa duda entre dos tipos, no ausencia. En el corpus, quitarlo mando a
# manual tres correos que estaban bien.
CAMPOS_QUE_EL_REVISOR_NO_QUITA = frozenset({"tipo_solicitud"})


def _reparos_de_identidad(revision: Any, valores: Dict[str, Any],
                          texto: str = "",
                          avisos: Optional[List[str]] = None) -> Dict[str, str]:
    """
    {campo: motivo} de lo que el revisor contradice entre los valores dados y
    hay que devolver al agente.

    Un CAMBIAR al mismo valor no contradice nada. El motivo lleva el valor que
    propone, para que el agente lo compruebe en el texto. No se devuelven:

    - los campos del remitente en que el revisor solo aconseja (quedan en
      `avisos`);
    - el nombre del remitente que el revisor quiere CAMBIAR cuando el que va
      ya es un nombre: entre quien firma y el nombre del buzon decide la
      compuerta;
    - el documento del afectado que el texto rotula como de identidad junto a
      su nombre: ahi lo determinista es prueba y la opinion del revisor no.
    """
    salida: Dict[str, str] = {}
    for campo, r in (getattr(revision, "reparos", None) or {}).items():
        if campo not in valores or not r.contradice:
            continue
        if campo in CAMPOS_EN_LOS_QUE_EL_REVISOR_SOLO_AVISA or (
                campo in CAMPOS_QUE_EL_REVISOR_NO_QUITA
                and r.veredicto != "CAMBIAR") or (
                campo == "nombre_remitente" and r.veredicto == "CAMBIAR"
                and not motivo_por_el_que_no_es_un_nombre(str(valores[campo]))):
            if avisos is not None:
                avisos.append(f"el revisor aconseja en {campo}: {r.veredicto} "
                              f"{r.valor[:60]} ({r.detalle[:100]})")
            continue
        if (campo == "numero_documento_afectado" and texto
                and compuerta.documento_junto_al_nombre(
                    valores[campo], valores.get("nombre_afectado"), texto)):
            if avisos is not None:
                avisos.append(
                    f"el revisor rechazo {campo}, pero el texto lo rotula como "
                    f"documento junto al nombre del afectado: se mantiene")
            continue
        # "No aparece" de un numero que el texto escribe pegado al nombre del
        # afectado es falso: el revisor no lo encontro (en el asunto, con
        # puntos en medio). Solo el SIN RESPALDO; un CAMBIAR propone otro.
        if (campo == "numero_documento_afectado" and texto
                and r.veredicto != "CAMBIAR"
                and compuerta.numero_pegado_al_nombre(
                    valores[campo], valores.get("nombre_afectado"), texto)):
            if avisos is not None:
                avisos.append(
                    f"el revisor no encontro {campo}, pero el texto lo escribe "
                    f"pegado al nombre del afectado: se mantiene")
            continue
        # El nombre del afectado que el texto da con su cedula rotulada
        # ("ASEGURADA X c.c. N"): que el correo diga "mi madre" no lo contradice,
        # y si es otra persona lo detectan las reglas fijas de la compuerta.
        if (campo in ("nombre_afectado", "tipo_documento_afectado") and texto
                and r.veredicto != "CAMBIAR"
                and valores.get("numero_documento_afectado")
                and compuerta.documento_junto_al_nombre(
                    valores["numero_documento_afectado"],
                    valores.get("nombre_afectado"), texto)):
            if avisos is not None:
                avisos.append(
                    f"el revisor rechazo {campo}, pero el texto da ese nombre con "
                    f"su documento rotulado: se mantiene")
            continue
        if (r.veredicto == "CAMBIAR" and r.valor
                and compuerta._normaliza(r.valor) == compuerta._normaliza(valores[campo])):
            continue
        propuesta = f" (propone '{r.valor[:60]}')" if r.valor else ""
        salida[campo] = (f"REVISOR_{r.veredicto.replace(' ', '_')}{propuesta}: "
                         f"{r.detalle or 'el revisor no lo encuentra respaldado'}")
    return salida


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
    # Lo que paso en la compuerta, la ronda de correccion y el revisor
    # obligatorio: veredicto inicial, campos devueltos, reparos del revisor y
    # cuantas llamadas costo. Lo lee el harness de punta a punta.
    revision: Dict[str, Any] = field(default_factory=dict)

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
        # Lo que tesseract no lee (fotos de celular, rotulos diminutos) lo
        # transcribe el mismo modelo, si tiene vision. Sin el, solo tesseract.
        from pipeline import conversion

        conversion.TRANSCRIPTOR_IA = getattr(modelo, "transcribir_imagen", None)
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
                    fallo = bitacora.registrar(
                        "CONVERSION", f"{entrada.nombre_original}: {entrada.detalle}",
                        cuerpo=entrada.detalle,
                    )
                    # Un adjunto que no se convierte es un adjunto perdido, no
                    # un correo perdido: la conversion es local y repetirla da
                    # lo mismo. Si lo que no se pudo clasificar se dejara
                    # fatal, el correo se aplazaria en cada corrida para
                    # siempre aunque ya tuviera todos sus datos.
                    if fallo.clase is DESCONOCIDO:
                        fallo.clase = POR_CODIGO["ADJUNTO_NO_LEIDO"]

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

                # Lo que el registro le devuelve al agente tambien es una
                # fuente: un nombre leido ahi esta sustentado aunque el correo
                # solo traiga la cedula. Se guarda para la compuerta.
                consultas_del_agente: List[Dict[str, Any]] = []

                def _consultar_y_anotar(documento, *resto):
                    datos = self._consultar_persona(documento, *resto)
                    if datos:
                        consultas_del_agente.append(datos)
                    return datos

                ctx = Contexto(
                    workspace=ws,
                    consultar_persona=_consultar_y_anotar,
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
                # y no en el radicador, para que la compuerta los valide. Aqui
                # van tambien las reglas fijas de identidad, que leen el correo.
                self._heredar_datos_del_afectado(
                    hallazgos, res,
                    fuentes=self._fuentes_del_correo(
                        email, cuerpo, texto_adjuntos, ws, consultas_del_agente),
                    correo_remitente=(email.from_address.address
                                      if email.from_address else ""))

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

                hallazgos, dictamen = await self._validar_y_corregir(
                    ctx, determinista, hallazgos, resolucion, bitacora, res,
                    email, cuerpo, texto_adjuntos, ws, consultas_del_agente)
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

    @staticmethod
    def _fuentes_del_correo(
        email: EmailMessage, cuerpo: str, texto_adjuntos: str, ws: Any,
        consultas: List[Dict[str, Any]],
    ) -> str:
        """
        Todo lo que el agente pudo leer, para que la compuerta compruebe que
        lo que propuso esta ahi: asunto, remitente, cuerpo, hilo, adjuntos y
        las respuestas del registro de personas.
        """
        partes = [email.subject or "", cuerpo or "", texto_adjuntos or ""]
        for direccion in (email.from_address, email.sender):
            if direccion:
                partes += [direccion.name or "", direccion.address or ""]
        try:
            partes.append(ws.texto_completo())
        except Exception:
            pass  # sin el workspace queda lo demas
        def _hojas(v: Any):
            if isinstance(v, dict):
                for x in v.values():
                    yield from _hojas(x)
            elif isinstance(v, (list, tuple)):
                for x in v:
                    yield from _hojas(x)
            elif isinstance(v, (str, int)) and not isinstance(v, bool):
                yield str(v)

        for datos in consultas:
            partes.extend(_hojas(datos))
        return "\n".join(partes)

    def _nombres_hilo(self, email: EmailMessage) -> List[Tuple[str, str]]:
        pares = []
        if email.from_address:
            pares.append((email.from_address.name, email.from_address.address))
        if email.sender:
            pares.append((email.sender.name, email.sender.address))
        return pares

    @staticmethod
    def _heredar_datos_del_afectado(
        hallazgos: Dict[str, Hallazgo], res: Resultado,
        fuentes: str = "", correo_remitente: str = "",
    ) -> None:
        """
        Reglas fijas de identidad, antes de la compuerta para que las valide:

        - el afectado que es una entidad va con su NIT y tipo NIT;
        - si el firmante es el afectado, su cedula es la del afectado;
        - el buzon de una entidad que se nombra en su dominio escribe por ella;
        - con `tipo_remitente == 3`, los datos del afectado pasan al remitente.

        La ultima solo con `tipo_remitente == 3`. Para un familiar o un
        tercero, copiar registra a otra persona con la cedula del trabajador;
        eso lo rechaza la compuerta y aqui no se hace.
        """
        from pipeline.radicador import TIPO_REMITENTE_ES_EL_AFECTADO

        if fuentes:
            texto = compuerta._texto_para_roles(fuentes)
            Orquestador._afectado_que_es_entidad(hallazgos, res, fuentes, texto)
            Orquestador._firmante_que_es_el_afectado(hallazgos, res, texto)
            Orquestador._entidad_que_escribe(
                hallazgos, res, fuentes, correo_remitente)

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

    @staticmethod
    def _poner(hallazgos, res, campo, valor, evidencia, confianza, metodo):
        anterior = hallazgos.get(campo)
        if anterior is not None and str(anterior.valor) == str(valor):
            return
        hallazgos[campo] = Hallazgo(campo=campo, valor=valor, evidencia=evidencia,
                                    confianza=confianza, metodo=metodo)
        res.atribucion[campo] = Origen.ALGORITMO
        res.avisos.append(
            f"regla de identidad {metodo}: {campo} "
            f"'{str(anterior.valor)[:40] if anterior else ''}' -> '{str(valor)[:40]}'")

    @staticmethod
    def _afectado_que_es_entidad(hallazgos, res, fuentes: str, texto: str) -> None:
        """
        El afectado es una entidad identificada con su NIT: tipo NIT y su NIT.

        Tambien cuando el nombre leido es el final del de la entidad ("CAMILO
        RUEDA" de "ESE CENTRO DE SALUD CAMILO RUEDA NIT ..."): sin esto la
        compuerta ve un nombre de persona con un NIT y lo manda a manual.
        Solo si el documento que se lleva no es ya de persona junto a ese nombre.
        """
        from utils.tipos_documento import TIPOS_DE_ORGANIZACION

        h_nom = hallazgos.get("nombre_afectado")
        if h_nom is None or not h_nom.valor:
            return
        h_doc = hallazgos.get("numero_documento_afectado")
        h_tipo = hallazgos.get("tipo_documento_afectado")
        try:
            tipo = int(h_tipo.valor) if h_tipo is not None else None
        except (TypeError, ValueError):
            tipo = None
        if (h_doc is not None and h_doc.valor and tipo not in TIPOS_DE_ORGANIZACION
                and compuerta.documento_junto_al_nombre(h_doc.valor, h_nom.valor, texto)):
            return
        hallada = compuerta.entidad_del_afectado(h_nom.valor, fuentes)
        if hallada is None:
            return
        nombre, nit = hallada
        evidencia = f"el afectado es la entidad '{nombre[:60]}', con NIT {nit} junto a su nombre"
        metodo = "afectado_que_es_una_entidad"
        Orquestador._poner(hallazgos, res, "nombre_afectado", nombre, evidencia,
                           max(h_nom.confianza, 0.85), metodo)
        Orquestador._poner(hallazgos, res, "numero_documento_afectado", nit,
                           evidencia, 0.9, metodo)
        Orquestador._poner(hallazgos, res, "tipo_documento_afectado",
                           int(TipoDocumento.NI), evidencia, 0.9, metodo)

    @staticmethod
    def _firmante_que_es_el_afectado(hallazgos, res, texto: str) -> None:
        """
        La cedula con que firma quien escribe es la del afectado si son la misma
        persona: el texto pone el nombre del afectado junto a ella con rotulo de
        identidad, o `tipo_remitente == 3`.

        Si el afectado ya tiene documento, solo se sustituye cuando el texto no
        lo da como suyo y el del firmante si: "identificado con el siniestro
        no. N" no es una cedula, y la del firmante esta junto a su nombre.
        """
        from pipeline.radicador import TIPO_REMITENTE_ES_EL_AFECTADO
        from utils.tipos_documento import TIPOS_DE_ORGANIZACION

        def valor(campo):
            h = hallazgos.get(campo)
            return h.valor if h is not None else None

        h_rem = hallazgos.get("numero_documento_remitente")
        nombre = valor("nombre_afectado")
        if (h_rem is None or not h_rem.valor or not nombre
                or h_rem.metodo == "remitente_es_el_afectado"
                or motivo_por_el_que_no_es_un_nombre(str(nombre))):
            return
        try:
            tipo_rem = int(valor("tipo_documento_remitente"))
        except (TypeError, ValueError):
            tipo_rem = None
        if tipo_rem in TIPOS_DE_ORGANIZACION:
            return  # un NIT no es la cedula de nadie
        rem = compuerta._digitos_ascii(h_rem.valor)
        af = compuerta._digitos_ascii(valor("numero_documento_afectado"))
        if not rem or rem == af:
            return
        junto = compuerta.documento_junto_al_nombre(rem, nombre, texto)
        es_tipo_3 = valor("tipo_remitente") == TIPO_REMITENTE_ES_EL_AFECTADO
        if not (junto or es_tipo_3):
            return
        if af and (not junto
                   or compuerta.documento_junto_al_nombre(af, nombre, texto)):
            return
        evidencia = (f"el firmante es el afectado: su documento {rem} aparece "
                     f"con rotulo de identidad junto al nombre del afectado"
                     if junto else
                     f"el remitente es el afectado y firma con el documento {rem}")
        metodo = "documento_del_firmante_que_es_el_afectado"
        Orquestador._poner(hallazgos, res, "numero_documento_afectado", rem,
                           evidencia, h_rem.confianza, metodo)
        if tipo_rem is not None:
            Orquestador._poner(hallazgos, res, "tipo_documento_afectado",
                               tipo_rem, evidencia, h_rem.confianza, metodo)

    @staticmethod
    def _tramite_de_la_empresa(hallazgos, res, email, cuerpo: str,
                               fuentes: str) -> None:
        """
        La empresa que pide algo para si misma es la afectada, con su NIT.

        El estado de cuenta, el paz y salvo, la habilitacion de un usuario, la
        asesoria o las copias de las afiliaciones de sus trabajadores son
        tramites de la empresa. Quien los firma —el representante legal, el
        gerente, la persona que escribe— es el remitente, no el afectado: su
        cedula junto a su nombre es correcta, pero radicar con ella deja el
        tramite de la empresa a nombre de una persona.

        Se aplica solo si el afectado elegido es esa persona (quien escribe, o
        alguien sin documento propio, o presentado como su representante) y el
        correo identifica a la organizacion que escribe con su NIT. Si no se
        encuentra el NIT, no se toca: la compuerta lo manda a revisar.
        """
        from utils.tipos_documento import TIPOS_DE_ORGANIZACION

        def valor(campo):
            h = hallazgos.get(campo)
            return h.valor if h is not None else None

        nombre = valor("nombre_afectado")
        if not nombre or not compuerta._es_nombre_de_persona(nombre):
            return
        try:
            tipo_af = int(valor("tipo_documento_afectado"))
        except (TypeError, ValueError):
            tipo_af = None
        principal = f"{email.subject or ''}\n{cuerpo or ''}"
        if not compuerta.tramite_de_la_empresa(principal):
            return
        buzon = email.from_address.name if email.from_address else ""
        correo = email.from_address.address if email.from_address else ""
        hallada = compuerta.empresa_con_nit(
            fuentes, valor("nombre_remitente"), correo, buzon)
        if hallada is None:
            return
        razon_social, nit = hallada
        texto = compuerta._texto_para_roles(fuentes)
        doc = compuerta._digitos_ascii(valor("numero_documento_afectado"))
        doc_rem = compuerta._digitos_ascii(valor("numero_documento_remitente"))
        remitente = valor("nombre_remitente")
        escribe = (valor("tipo_remitente") == 3
                   or (doc and doc == doc_rem)
                   or compuerta.es_la_misma_persona(
                       compuerta._normaliza(nombre), compuerta._normaliza(remitente))
                   or compuerta.es_la_misma_persona(
                       compuerta._normaliza(nombre), compuerta._normaliza(buzon)))
        sin_documento_propio = (not doc or tipo_af in TIPOS_DE_ORGANIZACION
                                or doc[:9] == nit)
        if not (escribe or sin_documento_propio
                or compuerta.presentado_como_representante(nombre, texto)):
            return

        evidencia = (f"la empresa pide un tramite propio: el afectado es "
                     f"'{razon_social[:60]}', con NIT {nit} en el correo; "
                     f"'{nombre}' firma por ella")
        metodo = "tramite_de_la_empresa"
        # Quien firmaba como afectado es quien escribe, si el remitente que
        # quedo es el buzon de la empresa.
        es_el_buzon_de_la_empresa = bool(remitente) and bool(
            compuerta._palabras_propias_de_entidad(str(remitente))
            & compuerta._palabras_propias_de_entidad(razon_social))
        if (escribe and not sin_documento_propio
                and (not remitente or compuerta._parece_entidad(remitente)
                     or es_el_buzon_de_la_empresa
                     or motivo_por_el_que_no_es_un_nombre(str(remitente)))):
            Orquestador._poner(hallazgos, res, "nombre_remitente", nombre,
                               evidencia, 0.85, metodo)
            if not doc_rem:
                Orquestador._poner(hallazgos, res, "numero_documento_remitente",
                                   doc, evidencia, 0.85, metodo)
                if tipo_af is not None:
                    Orquestador._poner(hallazgos, res, "tipo_documento_remitente",
                                       tipo_af, evidencia, 0.85, metodo)
        Orquestador._poner(hallazgos, res, "nombre_afectado", razon_social,
                           evidencia, 0.85, metodo)
        Orquestador._poner(hallazgos, res, "numero_documento_afectado", nit,
                           evidencia, 0.9, metodo)
        Orquestador._poner(hallazgos, res, "tipo_documento_afectado",
                           int(TipoDocumento.NI), evidencia, 0.9, metodo)
        Orquestador._poner(hallazgos, res, "tipo_remitente", 2,
                           evidencia, 0.85, metodo)
        # El sexo era el de la persona: una empresa no tiene.
        if hallazgos.pop("sexo_afectado", None) is not None:
            res.atribucion.pop("sexo_afectado", None)

    @staticmethod
    def _entidad_que_escribe(hallazgos, res, fuentes: str, correo: str) -> None:
        """
        Sin un remitente que sea un nombre, la entidad cuyo nombre es el
        dominio del buzon y que el texto identifica con su NIT.
        """
        h_nom = hallazgos.get("nombre_remitente")
        if h_nom is not None and h_nom.valor and not motivo_por_el_que_no_es_un_nombre(
                str(h_nom.valor)):
            return
        hallada = compuerta.entidad_del_dominio(correo, fuentes)
        if hallada is None:
            return
        nombre, nit = hallada
        evidencia = (f"escribe desde el dominio de '{nombre[:60]}', que el "
                     f"texto identifica con NIT {nit}")
        metodo = "entidad_del_dominio_del_remitente"
        Orquestador._poner(hallazgos, res, "nombre_remitente", nombre,
                           evidencia, 0.85, metodo)
        h_doc = hallazgos.get("numero_documento_remitente")
        if h_doc is None or not h_doc.valor:
            Orquestador._poner(hallazgos, res, "numero_documento_remitente", nit,
                               evidencia, 0.85, metodo)
            Orquestador._poner(hallazgos, res, "tipo_documento_remitente",
                               int(TipoDocumento.NI), evidencia, 0.85, metodo)
        if "tipo_remitente" not in hallazgos:
            Orquestador._poner(hallazgos, res, "tipo_remitente", 2,
                               evidencia, 0.85, metodo)

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

    async def _validar_y_corregir(self, ctx, determinista, hallazgos, resolucion,
                                  bitacora, res, email, cuerpo, texto_adjuntos,
                                  ws, consultas_del_agente):
        """
        Compuerta, ronda de correccion y revision obligatoria de la identidad.
        Devuelve (hallazgos, dictamen), sin aplicar las correcciones de la
        compuerta.

        1. La compuerta valida. Lo que rechaza suele tener arreglo —el agente
           puso el area en vez de la entidad, o el NIT donde iba la cedula—: se
           le devuelve al agente UNA vez, con cada reparo dicho, y lo corregido
           vuelve a pasar por la misma compuerta.
        2. Si la compuerta lo dejaria pasar, el revisor comprueba la identidad.

        Antes de todo, la regla fija del tramite de la empresa: va aqui y no con
        las demas reglas de identidad porque necesita el asunto y el cuerpo por
        separado de los adjuntos.
        """
        self._tramite_de_la_empresa(
            hallazgos, res, email, cuerpo,
            self._fuentes_del_correo(email, cuerpo, texto_adjuntos, ws,
                                     consultas_del_agente))
        with res.cronometrar("compuerta"):
            dictamen = self._validar_de_nuevo(
                hallazgos, ctx, resolucion, email, cuerpo, texto_adjuntos, ws,
                consultas_del_agente)
        res.revision["veredicto_antes_de_corregir"] = dictamen.veredicto.value
        if (dictamen.veredicto is Veredicto.RECHAZADO
                and not self._sin_tiempo()):
            a_corregir = campos_a_corregir(dictamen, determinista)
            if a_corregir:
                res.revision["corregidos"] = dict(a_corregir)
                res.revision["rechazos_antes_de_corregir"] = [
                    f"{r.codigo}: {r.campo}: {r.detalle}"
                    for r in dictamen.bloqueantes]
                with res.cronometrar("correccion"):
                    hallazgos, dictamen = await self._corregir(
                        ctx, determinista, hallazgos, a_corregir,
                        bitacora, res, email, cuerpo, texto_adjuntos,
                        ws, consultas_del_agente, resolucion)
        if dictamen.veredicto is not Veredicto.RECHAZADO and not self._sin_tiempo():
            with res.cronometrar("revisor"):
                hallazgos, dictamen = await self._revision_obligatoria(
                    ctx, determinista, hallazgos, dictamen, resolucion,
                    bitacora, res, email, cuerpo, texto_adjuntos, ws,
                    consultas_del_agente)
        return hallazgos, dictamen

    def _valores_de_identidad(self, hallazgos, dictamen) -> Dict[str, Any]:
        """Lo que se enviaria de cada campo de identidad, ya corregido."""
        enviados = compuerta.aplicar(hallazgos, dictamen)
        return {c: enviados[c].valor for c in CAMPOS_REVISADOS
                if c in enviados and enviados[c].valor not in (None, "", [])}

    def _llamar_al_revisor(self, ws, valores, hallazgos, fuentes, email, res):
        """
        Una llamada al revisor sobre los valores de identidad, o None si fallo.

        Si falla no bloquea: deja aviso y la compuerta decide sola, como antes
        de que existiera.
        """
        from pipeline import revisor as revisor_mod

        texto = compuerta._texto_para_roles(fuentes)
        notas = {}
        for campo, v in valores.items():
            partes = []
            h = hallazgos.get(campo)
            if h is not None and h.evidencia:
                partes.append(str(h.evidencia)[:160])
            if campo.startswith(("numero_documento", "nombre")):
                partes += extractos_del_valor(v, texto)
            catalogo = CATALOGOS.get(
                campo if campo in ("tipo_remitente", "tipo_solicitud")
                else "tipo_documento" if campo.startswith("tipo_documento")
                else "", {})
            try:
                descripcion = catalogo.get(int(v)) if catalogo else None
            except (TypeError, ValueError):
                descripcion = None
            if descripcion:
                partes.insert(0, f"el codigo {v} es '{descripcion}'")
            if partes:
                notas[campo] = " || ".join(partes)
        contexto = "\n".join(filter(None, (
            f"ASUNTO: {email.subject or ''}",
            (f"REMITENTE: {email.from_address.name or ''} "
             f"<{email.from_address.address or ''}>"
             if email.from_address else ""))))
        try:
            esquemas = [e for e in ESQUEMAS_HERRAMIENTAS
                        if e["name"] in revisor_mod.HERRAMIENTAS_DEL_REVISOR]
            revision = revisor_mod.revisar(
                self.modelo, Contexto(workspace=ws), valores, esquemas,
                notas=notas, contexto=contexto)
        except Exception as e:
            res.avisos.append(
                f"el revisor no se pudo ejecutar y se sigue con la compuerta: "
                f"{type(e).__name__}: {str(e)[:120]}")
            return None
        res.tokens_entrada += revision.tokens_entrada
        res.tokens_salida += revision.tokens_salida
        res.revision["llamadas"] = res.revision.get("llamadas", 0) + 1
        res.revision["vueltas"] = res.revision.get("vueltas", 0) + revision.vueltas
        if revision.ilegibles:
            res.avisos.append(
                "el revisor respondio lineas que no se entienden: "
                + " / ".join(revision.ilegibles[:3]))
        return revision

    async def _revision_obligatoria(self, ctx, determinista, hallazgos, dictamen,
                                    resolucion, bitacora, res, email, cuerpo,
                                    texto_adjuntos, ws, consultas_del_agente):
        """
        Antes de radicar, el revisor comprueba la identidad que se va a enviar,
        venga del algoritmo, del agente o del registro.

        Lo que rechaza vuelve al agente por la misma ronda de correccion, con
        el motivo. Lo corregido pasa otra vez por la compuerta y, si cambio un
        campo de identidad, por el revisor una vez mas. Lo que no se corrige no
        se envia: si era obligatorio, el correo va a manual.
        """
        fuentes = self._fuentes_del_correo(
            email, cuerpo, texto_adjuntos, ws, consultas_del_agente)
        valores = self._valores_de_identidad(hallazgos, dictamen)
        if not valores:
            return hallazgos, dictamen
        revision = await asyncio.to_thread(
            self._llamar_al_revisor, ws, valores, hallazgos, fuentes, email, res)
        if revision is None:
            return hallazgos, dictamen
        reparos = _reparos_de_identidad(
            revision, valores, compuerta._texto_para_roles(fuentes), res.avisos)
        res.revision["reparos"] = dict(reparos)
        if not reparos:
            res.avisos.append("el revisor dio por buena la identidad")
            return hallazgos, dictamen
        res.avisos.append(
            "el revisor rechazo " + ", ".join(
                f"{c} ({m})" for c, m in sorted(reparos.items())))

        enviados = compuerta.aplicar(hallazgos, dictamen)
        if self._sin_tiempo():
            nuevos = {c: h for c, h in enviados.items() if c not in reparos}
            return nuevos, self._validar_de_nuevo(
                nuevos, ctx, resolucion, email, cuerpo, texto_adjuntos, ws,
                consultas_del_agente)
        with res.cronometrar("correccion"):
            nuevos, dictamen = await self._corregir(
                ctx, determinista, enviados, reparos, bitacora, res, email,
                cuerpo, texto_adjuntos, ws, consultas_del_agente, resolucion)

        # El tipo de solicitud es obligatorio y siempre hay uno que poner: si
        # el agente no propone otro, se queda el que habia. Quitarlo manda a
        # manual un correo que solo estaba en duda entre dos tipos.
        for campo in CAMPOS_QUE_EL_REVISOR_NO_QUITA & set(reparos):
            if campo not in nuevos and campo in enviados:
                nuevos[campo] = enviados[campo]
                res.atribucion[campo] = Origen.IA
                res.avisos.append(
                    f"el agente no propuso otro {campo}: se mantiene el que habia")
        # El agente repitio el valor rechazado: no lo corrigio.
        repetidos = [c for c in reparos if c in nuevos
                     and c not in CAMPOS_QUE_EL_REVISOR_NO_QUITA
                     and str(nuevos[c].valor) == str(valores.get(c))]
        if any(c in CAMPOS_QUE_EL_REVISOR_NO_QUITA for c in reparos) and not repetidos:
            dictamen = self._validar_de_nuevo(
                nuevos, ctx, resolucion, email, cuerpo, texto_adjuntos, ws,
                consultas_del_agente)
        for campo in repetidos:
            nuevos.pop(campo, None)
            res.avisos.append(
                f"el agente repitio el valor que el revisor rechazo en {campo}: "
                f"no se envia")
        if repetidos:
            dictamen = self._validar_de_nuevo(
                nuevos, ctx, resolucion, email, cuerpo, texto_adjuntos, ws,
                consultas_del_agente)
        if dictamen.veredicto is Veredicto.RECHAZADO:
            return nuevos, dictamen

        # Lo que el agente cambio al valor que el propio revisor propuso ya esta
        # revisado: preguntarle otra vez solo le da ocasion de desdecirse.
        propuestos = {c: compuerta._normaliza(r.valor)
                      for c, r in revision.reparos.items()
                      if r.veredicto == "CAMBIAR" and r.valor}
        cambiados = {c: v for c, v in self._valores_de_identidad(
            nuevos, dictamen).items()
            if str(valores.get(c)) != str(v)
            and propuestos.get(c) != compuerta._normaliza(v)}
        if not cambiados or self._sin_tiempo():
            return nuevos, dictamen
        segunda = await asyncio.to_thread(
            self._llamar_al_revisor, ws, cambiados, nuevos,
            self._fuentes_del_correo(email, cuerpo, texto_adjuntos, ws,
                                     consultas_del_agente), email, res)
        if segunda is None:
            return nuevos, dictamen
        otra_vez = _reparos_de_identidad(
            segunda, {**valores, **cambiados}, compuerta._texto_para_roles(fuentes),
            res.avisos)
        otra_vez = {c: m for c, m in otra_vez.items()
                    if c in cambiados and c not in CAMPOS_QUE_EL_REVISOR_NO_QUITA}
        res.revision["reparos_tras_corregir"] = dict(otra_vez)
        if not otra_vez:
            res.avisos.append("el revisor dio por buena la correccion")
            return nuevos, dictamen
        res.avisos.append(
            "el revisor rechazo tambien la correccion de "
            + ", ".join(sorted(otra_vez)) + ": no se envia")
        enviados = compuerta.aplicar(nuevos, dictamen)
        for campo in otra_vez:
            enviados.pop(campo, None)
            res.atribucion[campo] = Origen.NO_RESUELTO
        return enviados, self._validar_de_nuevo(
            enviados, ctx, resolucion, email, cuerpo, texto_adjuntos, ws,
            consultas_del_agente)

    def _sin_tiempo(self) -> bool:
        """Si a la corrida ya no le queda presupuesto para seguir con este correo."""
        return self.presupuesto is not None and self.presupuesto.agotado()

    async def _corregir(self, ctx, determinista, hallazgos, a_corregir,
                        bitacora, res, email, cuerpo, texto_adjuntos, ws,
                        consultas_del_agente, resolucion_previa):
        """
        Devuelve al agente los campos rechazados, con el motivo de cada uno, y
        vuelve a validar. Devuelve (hallazgos, dictamen).

        Lo que el agente no corrija queda sin valor y la compuerta decide igual
        que antes: si era obligatorio, va a manual con el motivo de siempre.
        Si se corrige el documento del afectado, lo que el registro relleno con
        el documento anterior se descarta y se vuelve a consultar.
        """
        correccion = extraccion_para_corregir(determinista, hallazgos, a_corregir)
        res.avisos.append(
            "la validacion rechazo "
            + ", ".join(f"{c} ({m})" for c, m in sorted(a_corregir.items()))
            + ": se le devuelve al agente para corregir")
        try:
            resolucion = await self._resolver_con_reintento(
                ctx, correccion, bitacora, res)
        except Exception as e:
            res.avisos.append(f"la correccion no se pudo hacer: {e}")
            return hallazgos, self._validar_de_nuevo(
                hallazgos, ctx, resolucion_previa, email, cuerpo,
                texto_adjuntos, ws, consultas_del_agente)
        res.tokens_entrada += resolucion.tokens_entrada
        res.tokens_salida += resolucion.tokens_salida
        res.turnos_del_agente += resolucion.iteraciones
        res.avisos.extend(resolucion.descartados)

        nuevos = combinar(correccion, resolucion)
        for campo in a_corregir:
            if campo in resolucion.hallazgos:
                res.atribucion[campo] = Origen.IA
                res.avisos.append(
                    f"corregido {campo}: "
                    f"'{str(hallazgos.get(campo).valor if hallazgos.get(campo) else '')[:60]}'"
                    f" -> '{str(resolucion.hallazgos[campo].valor)[:60]}'")
            else:
                res.atribucion[campo] = Origen.NO_RESUELTO
        if "numero_documento_afectado" in a_corregir:
            for campo, origen in list(res.atribucion.items()):
                if origen is Origen.REGISTRO and campo not in resolucion.hallazgos:
                    nuevos.pop(campo, None)
            contradicciones: List[str] = []
            for campo, hallazgo in rellenar_desde_el_registro(
                nuevos, self._consultar_persona_del_afectado,
                res.avisos, contradicciones,
                texto=f"{cuerpo}\n{texto_adjuntos}",
                principal=f"{email.subject or ''}\n{cuerpo}",
            ).items():
                nuevos[campo] = hallazgo
                res.atribucion[campo] = Origen.REGISTRO
            res.avisos.extend(contradicciones)
        dictamen = self._validar_de_nuevo(
            nuevos, ctx, resolucion, email, cuerpo, texto_adjuntos, ws,
            consultas_del_agente)
        res.avisos.append(
            f"tras la correccion la validacion dice: {dictamen.veredicto.value}")
        return nuevos, dictamen

    def _validar_de_nuevo(self, hallazgos, ctx, resolucion, email, cuerpo,
                          texto_adjuntos, ws, consultas_del_agente):
        try:
            nombres_adjuntos = [e.nombre_original for e in ws.manifiesto.entradas]
        except AttributeError:
            nombres_adjuntos = []
        return compuerta.validar(
            hallazgos, ctx.catalogos,
            motivos_no_resueltos={
                campo: nr.motivo
                for campo, nr in resolucion.sin_resolver.items()
            },
            fuentes=self._fuentes_del_correo(
                email, cuerpo, texto_adjuntos, ws, consultas_del_agente),
            principal=f"{email.subject or ''}\n{cuerpo or ''}",
            nombres_adjuntos=nombres_adjuntos,
        )

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
