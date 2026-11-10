"""
Deteccion de correos ya radicados.

Seis comprobaciones, en orden de costo, todas antes de llamar al modelo: un
duplicado no llega a gastar ni una llamada.

`comprobar` corre las cuatro primeras, sin descargar adjuntos:

  1. la etiqueta del propio correo, que no cuesta nada;
  2. si el hilo ya se proceso en esta misma ejecucion, sin salir a la red;
  3. la etiqueta de cualquier correo del mismo hilo, una consulta a Graph;
  4. un correo radicado con el mismo asunto que sea el mismo caso, otra consulta
     a Graph. Hace falta porque un reenvio interno abre un `conversationId`
     nuevo y la capa del hilo deja de ver el radicado.

`comprobar_contenido` corre las dos ultimas, que necesitan los nombres de los
adjuntos:

  5. el hash del contenido contra la cache;
  6. remitente y asunto contra la auditoria.

El asunto no identifica un caso, y el par remitente y asunto tampoco: bajo un
asunto generico entran PQRD de personas distintas, y PQRD distintas de la misma
persona. Por eso la cuarta exige ademas `es_el_mismo_caso`.

La sexta consulta una tabla historica que este pipeline no alimenta, asi que
solo responde por correos anteriores a lo que ella registra: un registro mas
viejo que el correo no puede ser ese correo. Si hay cache, su respuesta manda
sobre la de la auditoria. El asunto se limpia antes de preguntar, porque un
reenvio cambia el texto sin cambiar el caso.

Cada servicio se inyecta y puede faltar. Si falta o falla, el correo se
procesa igual y la capa perdida queda registrada en `motivo`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Dict, List, Set, Tuple

from models.email import EmailMessage
from utils.generic_filter import es_buzon_de_positiva, remitentes_externos_citados
from utils.hash import limpiar_asunto
from utils.radicado import etiqueta_de_radicado

# Cuanto se tienen que parecer dos textos para ser el mismo correo enviado otra
# vez. Por debajo, la misma persona esta diciendo otra cosa.
PARECIDO_DEL_MISMO_TEXTO = 0.9
# Lo que se compara del texto: lo que define el tramite va arriba.
LARGO_DEL_TEXTO_COMPARADO = 1500
# Un texto mas corto ("adjunto documentos") es igual en correos de casos
# distintos, y lo que los distingue son los adjuntos.
LARGO_DE_UN_TEXTO_QUE_DISTINGUE = 40

_HILO_CITADO = re.compile(r"\n[ \t>]*(?:De|From)\s*:.*", re.IGNORECASE | re.DOTALL)


def texto_propio(cuerpo: str) -> str:
    """El texto que escribio quien envia, sin el hilo citado debajo."""
    sin_hilo = _HILO_CITADO.sub("", f"\n{cuerpo or ''}")
    return " ".join(sin_hilo.split()).lower()[:LARGO_DEL_TEXTO_COMPARADO]


def textos_iguales(uno: str, otro: str) -> bool:
    """Si dos textos propios son el mismo correo enviado otra vez."""
    a, b = texto_propio(uno), texto_propio(otro)
    return SequenceMatcher(None, a, b).ratio() >= PARECIDO_DEL_MISMO_TEXTO


def texto_que_distingue(cuerpo: str) -> bool:
    """Si el texto propio alcanza para distinguir un correo de otro."""
    return len(texto_propio(cuerpo)) >= LARGO_DE_UN_TEXTO_QUE_DISTINGUE


def estan_vinculados(remitente: str, cuerpo: str, otro_remitente: str, otro_cuerpo: str) -> bool:
    """
    Si dos correos de remitentes distintos son el mismo caso reenviado.

    Un reenvio arrastra a quien escribio, asi que un correo nombra al remitente
    del otro o los dos citan al mismo. Sin ese vinculo, compartir el asunto solo
    dice que usaron el mismo asunto generico.

    Que los dos citen al mismo remitente solo vale si uno de ellos es un area
    de la entidad, que es quien reenvia lo que escribio otro. Dos personas de
    afuera que citan al mismo remitente estan respondiendo cada una a la misma
    comunicacion masiva ("Le acompañamos en la normalizacion de su cartera",
    "Comunicacion Cartera Preventiva"): son casos distintos, y en produccion
    se estaban archivando como duplicados del radicado de otra persona.
    """
    de, otro = (remitente or "").strip().lower(), (otro_remitente or "").strip().lower()
    if not de or not otro:
        return False
    if otro in (cuerpo or "").lower() or de in (otro_cuerpo or "").lower():
        return True
    if not (es_buzon_de_positiva(de) or es_buzon_de_positiva(otro)):
        return False
    return bool(remitentes_externos_citados(cuerpo) & remitentes_externos_citados(otro_cuerpo))


def _dedup_por_red_apagada() -> bool:
    """
    Si las consultas de deduplicacion contra Graph estan apagadas.

    Solo se usa para anotarlo en `motivo`. Quien las apaga es
    `services/outlook.py`.
    """
    try:
        from config.settings import dedup_desactivada

        return dedup_desactivada()
    except Exception:
        return False


def _remitente(email: EmailMessage) -> str:
    return ((email.from_address.address if email.from_address else "") or "").strip().lower()


def _firma_de_documentos(adjuntos) -> List[Tuple[str, int]]:
    """Los documentos del correo por nombre normalizado y tamaño, en orden."""
    from pipeline.anexos import es_documento, nombre_normalizado

    return sorted(
        (nombre_normalizado(a.get("name") or ""), int(a.get("size") or 0))
        for a in adjuntos or [] if es_documento(a))


@dataclass
class Veredicto:
    """Resultado de la comprobacion. `motivo` explica cual capa lo detecto."""

    es_duplicado: bool = False
    radicado: str = ""
    motivo: str = ""
    # El radicado del caso cuando el correo es de su hilo pero trae documentos
    # que el radicado no tiene. No es un duplicado: se radica como anexo.
    anexo_de: str = ""
    documentos_nuevos: List[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.es_duplicado


def hash_del_correo(email: EmailMessage, nombres_adjuntos=None) -> str:
    """
    Llave de contenido: asunto sin prefijos de reenvio, remitente y adjuntos.

    Sin limpiar el asunto, "RV: RE: Solicitud" y "Solicitud" darian llaves
    distintas y el mismo caso se radicaria una vez por cada reenvio interno.
    """
    from utils.hash import generate_email_hash

    return generate_email_hash(
        email.subject or "",
        (email.from_address.address if email.from_address else ""),
        "|".join(sorted(nombres_adjuntos or [])),
    )


@dataclass
class Detector:
    """
    Comprueba si un correo ya fue radicado.

    Los tres servicios se inyectan y pueden ser None. Sin ellos se pierden sus
    capas, pero las que no dependen de red siguen funcionando y la perdida
    queda anotada en el motivo en vez de dar por bueno el correo.
    """

    outlook: Any = None
    cache: Any = None          # `email_hash_exists` / `save_email_hash`
    auditoria: Any = None      # `email_exists`
    vistos: Set[str] = field(default_factory=set)
    # Lo radicado en esta corrida, por remitente: (correo, radicado).
    del_lote: Dict[str, List[Tuple[EmailMessage, str]]] = field(default_factory=dict)

    def recordar_del_lote(self, email: EmailMessage, radicado: str) -> None:
        """Anota un radicado de esta corrida para compararlo con lo que siga."""
        remitente = _remitente(email)
        if remitente and radicado:
            self.del_lote.setdefault(remitente, []).append((email, radicado))

    async def comprobar(self, email: EmailMessage) -> Veredicto:
        etiqueta = etiqueta_de_radicado(getattr(email, "categories", None))
        if etiqueta:
            return Veredicto(True, etiqueta, "el correo ya tiene etiqueta de radicado")

        conversacion = getattr(email, "conversation_id", None)
        if conversacion and conversacion in self.vistos:
            return Veredicto(
                True, "", "otro correo del mismo hilo ya se proceso en esta ejecucion")

        # El hilo queda marcado pase lo que pase con las consultas: si una capa
        # se cae, la de "ya lo vimos en este lote" tiene que seguir en pie.
        if conversacion:
            self.vistos.add(conversacion)

        degradaciones = []
        repetido, fallo = await self._radicado_en_esta_corrida(email)
        if fallo:
            degradaciones.append(fallo)
        elif repetido:
            return repetido
        if conversacion and self.outlook is not None:
            encontrado, fallo = await self._preguntar(
                self.outlook, "hilo_ya_radicado",
                "la comprobacion del hilo", conversacion)
            if fallo:
                # Degradar, no bloquear: si Graph no responde se procesa el
                # correo. El riesgo de un duplicado es menor que el de dejar
                # de radicar todo el lote, pero tiene que constar.
                degradaciones.append(fallo)
            elif encontrado:
                return await self._salvo_documentos_nuevos(
                    email, str(encontrado),
                    "otro correo del mismo hilo ya fue radicado")

        # Cuando un area interna reenvia, Outlook abre un conversationId nuevo
        # y la capa del hilo deja de ver el radicado. El asunto limpio si
        # sobrevive al reenvio.
        if self.outlook is not None and (email.subject or "").strip():
            encontrado, fallo = await self._preguntar(
                self.outlook, "buscar_etiqueta_ent_por_asunto",
                "el asunto ya radicado", email.subject or "", email)
            if fallo:
                degradaciones.append(fallo)
            elif encontrado:
                return await self._salvo_documentos_nuevos(
                    email, str(encontrado),
                    "ya hay un correo radicado con este mismo asunto")

        if self.outlook is not None and _dedup_por_red_apagada():
            degradaciones.append(
                "MODO_TEST: la deteccion por hilo y por asunto esta "
                "desactivada; para ejercitarla, DEDUP_EN_PRUEBAS=true")
        return Veredicto(False, "", " | ".join(degradaciones))

    async def _radicado_en_esta_corrida(self, email: EmailMessage):
        """
        Si el mismo remitente ya radico este caso en esta misma corrida.

        Las capas que consultan Graph lo encuentran por la etiqueta del
        radicado, que acaba de escribirse; esta lo compara directo, sin
        depender de cuanto tarde Graph en mostrarla.

        Con el mismo asunto se usa el criterio de la capa del asunto
        (`_es_el_mismo_caso`). Con otro asunto se exige mas, porque el asunto ya
        no dice que sea el mismo caso: el mismo texto propio, que distinga, y
        exactamente los mismos documentos, al menos uno.
        """
        previos = self.del_lote.get(_remitente(email)) or []
        if not previos or self.outlook is None:
            return None, ""
        from utils.hash import asunto_comparable

        mismo_caso = getattr(self.outlook, "_es_el_mismo_caso", None)
        adjuntos = getattr(self.outlook, "get_attachments", None)
        asunto = asunto_comparable(email.subject or "")
        motivo = "el mismo remitente radico este caso en esta misma corrida"
        try:
            for previo, radicado in previos:
                if asunto_comparable(previo.subject or "") == asunto:
                    if mismo_caso is not None and await mismo_caso(email, {
                            "id": previo.id,
                            "from": {"emailAddress": {"address": _remitente(previo)}}}):
                        return await self._salvo_documentos_nuevos(
                            email, radicado, motivo), ""
                    continue
                if adjuntos is None or not (
                        texto_que_distingue(email.body.content or "")
                        and textos_iguales(email.body.content or "",
                                           previo.body.content or "")):
                    continue
                propios = _firma_de_documentos(await adjuntos(email.id))
                if propios and propios == _firma_de_documentos(await adjuntos(previo.id)):
                    return Veredicto(True, radicado, motivo), ""
        except Exception as e:
            return None, f"la comparacion con esta corrida fallo: {type(e).__name__}: {e}"
        return None, ""

    async def _salvo_documentos_nuevos(
        self, email: EmailMessage, radicado: str, motivo: str,
    ) -> Veredicto:
        """
        Duplicado del radicado, salvo que traiga documentos que el radicado no tiene.

        Solo se mira si escribe alguien de afuera: lo que llega de un buzon de
        la entidad en el hilo de un caso es un reenvio o la respuesta que se le
        dio, con la carta de respuesta adjunta, y radicarlo abriria otro caso
        con la respuesta de la entidad como si fuera una peticion.

        Si la comparacion no se puede hacer, se queda como duplicado: radicar
        sin saber si el documento es nuevo es como se radica dos veces.
        """
        remitente = email.from_address.address if email.from_address else ""
        comparar = getattr(self.outlook, "documentos_nuevos_frente_al_radicado", None)
        if (comparar is None or not getattr(email, "has_attachments", False)
                or not remitente or es_buzon_de_positiva(remitente)):
            return Veredicto(True, radicado, motivo)
        nuevos, fallo = await self._preguntar(
            self.outlook, "documentos_nuevos_frente_al_radicado",
            "la comparacion de documentos", email, radicado)
        if fallo:
            return Veredicto(True, radicado, f"{motivo} (sin comparar documentos: {fallo})")
        if not nuevos:
            return Veredicto(True, radicado, motivo)
        return Veredicto(False, "", "", anexo_de=radicado,
                         documentos_nuevos=list(nuevos))

    async def comprobar_contenido(
        self, email: EmailMessage, nombres_adjuntos=None
    ) -> Veredicto:
        """
        Capas 5 y 6, ya con los adjuntos descargados.

        El hash incluye los nombres de los adjuntos: el mismo asunto con otros
        soportes es otro caso.
        """
        degradaciones = []

        # `cache=None` significa que esta capa no se usa, y eso no es una
        # degradacion: no hay nada que avisar en cada correo. Un fallo del
        # servicio, en cambio, si aparece.
        el_contenido_responde = False
        if self.cache is not None:
            llave = hash_del_correo(email, nombres_adjuntos)
            existe, fallo = await self._preguntar(
                self.cache, "email_hash_exists", "la cache de hashes", llave)
            if fallo:
                degradaciones.append(fallo)
            elif existe:
                return Veredicto(
                    True, "", f"el contenido ya se proceso (hash {llave[:12]})")
            else:
                # La capa del contenido hablo y dijo que este correo no se ha
                # visto. Eso es mas especifico que el par remitente+asunto.
                el_contenido_responde = True

        remitente = email.from_address.address if email.from_address else ""
        # Un buzon de Positiva no identifica a quien pide: la app
        # (appconexionpositiva@) manda todas las PQRD con el mismo remitente y
        # el mismo asunto, y las areas reenvian con el suyo. Ahi el par
        # remitente+asunto junta a personas distintas en un solo radicado.
        if remitente and not el_contenido_responde and not es_buzon_de_positiva(remitente):
            # El asunto va limpio: un reenvio le antepone "RV:" y cambia el
            # texto sin cambiar el caso, y con el prefijo la consulta no
            # encuentra el radicado que si existe.
            existe, fallo = await self._preguntar(
                self.auditoria, "email_exists", "la auditoria",
                remitente, limpiar_asunto(email.subject or ""),
                getattr(email, "received_datetime", "") or "")
            if fallo:
                degradaciones.append(fallo)
            elif existe:
                return Veredicto(
                    True, "", "ya hay un registro con este remitente y asunto")

        return Veredicto(False, "", " | ".join(degradaciones))

    async def _preguntar(self, servicio, metodo, nombre, *args):
        """
        Consulta una capa. Devuelve (respuesta, motivo_de_degradacion).

        La respuesta se devuelve TAL CUAL, sin convertir a bool: unas capas
        contestan si o no y otras devuelven el numero de radicado, que hay que
        conservar para poder decir cual es el correo que ya existe.
        """
        if servicio is None:
            return None, f"{nombre} no esta configurada"
        fn = getattr(servicio, metodo, None)
        if fn is None:
            return None, f"{nombre} no expone {metodo}"
        try:
            respuesta = await fn(*args)
        except Exception as e:
            return None, f"{nombre} fallo: {type(e).__name__}: {e}"
        # Redis y la base de radicados no propagan: devuelven False tanto si no hay
        # duplicado como si la consulta se cayo. Sin mirar `ultimo_error`, una
        # capa muerta se ve exactamente igual que una capa que dijo "todo
        # limpio", y una caida prolongada podria pasar sin aparecer en ningun
        # informe.
        fallo = getattr(servicio, "ultimo_error", "")
        if fallo:
            return None, f"{nombre} fallo: {fallo}"
        return respuesta, ""

    async def registrar_radicado(
        self, email: EmailMessage, nombres_adjuntos=None
    ) -> str:
        """
        Guarda el hash tras radicar, para que la capa 4 sirva de algo.

        Si no se guarda, la cache nunca se llena y esa capa no detecta nada.
        Devuelve el motivo si no se pudo guardar, o cadena vacia.
        """
        self.registrar(email)
        if self.cache is None:
            # La capa no esta en uso: no hay hash que guardar ni nada que avisar.
            return ""
        guardar = getattr(self.cache, "save_email_hash", None)
        if guardar is None:
            return "la cache de hashes no expone save_email_hash"
        try:
            await guardar(hash_del_correo(email, nombres_adjuntos))
        except Exception as e:
            return f"no se guardo el hash: {type(e).__name__}: {e}"
        return ""

    async def guardar_en_auditoria(
        self, email: EmailMessage, radicado: str, documento_afectado: str = ""
    ) -> str:
        """
        Guarda el radicado en la tabla de radicados, la que consulta la capa 6.

        Sin esto esa capa nunca encuentra nada de lo que radica este pipeline.
        Nunca bloquea: la radicacion ya ocurrio. Devuelve el motivo si no se
        pudo guardar, o cadena vacia.
        """
        if self.auditoria is None or not radicado or radicado == "DRY-RUN":
            return ""
        guardar = getattr(self.auditoria, "guardar_radicado", None)
        if guardar is None:
            return ""
        try:
            await guardar(
                numero_radicado=radicado,
                asunto=email.subject or "",
                # Tal cual lo consulta la capa 6, que compara por igualdad.
                correo_remitente=(email.from_address.address if email.from_address else "") or "",
                numero_documento_afectado=documento_afectado or "",
            )
        except Exception as e:
            return f"no se guardo en la tabla de radicados: {type(e).__name__}: {e}"
        return ""

    def registrar(self, email: EmailMessage) -> None:
        """Marca el hilo como atendido en esta ejecucion."""
        conversacion = getattr(email, "conversation_id", None)
        if conversacion:
            self.vistos.add(conversacion)
