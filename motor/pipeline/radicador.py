"""
Armado del payload y envio a SGDEA.

Traduce los hallazgos del pipeline a `RadicacionPayload` y delega el envio en
`RadicacionService`. Los reintentos se deciden con la taxonomia de
`pipeline.errores`.
"""
from __future__ import annotations

import asyncio
import html
from dataclasses import dataclass, field  # noqa: F401
from typing import Any, Dict, List, Optional, Tuple

from business_rules.geo_mapping import DEPARTAMENTOS_MAP
from models.email import EmailMessage
from models.enums import TipoSolicitud
from models.persona import PersonaData
from models.radicacion import RadicacionPayload
from pipeline.errores import (
    Bitacora,
    clasificar,
    debe_reintentar,
    espera_para,
    puede_repetirse_una_escritura,
)
from pipeline.extraccion import Hallazgo
from pipeline.salida import AVISO, INFO, datos_del_correo, emitir
from utils.contacto_extractor import normalizar_telefono
from utils.tipos_documento import solo_digitos

# Valores por defecto cuando el campo no se pudo determinar. Cada uso queda
# anotado en `suposiciones` y aparece en el informe.
SEXO_POR_DEFECTO = 3             # Otro o no determinado
TIPO_REMITENTE_TERCERO = 2
# Cedula de ciudadania. Se usa cuando el remitente no trae tipo propio: el del
# afectado no se hereda, porque puede ser el NIT de una empresa.
TIPO_DOCUMENTO_POR_DEFECTO = 1
CANAL_EMAIL_IA = 7
# SGDEA exige departamento y municipio; los IDs salen de
# `business_rules.geo_mapping`.
DEPARTAMENTO_POR_DEFECTO = DEPARTAMENTOS_MAP["BOGOTA"]
MUNICIPIO_POR_DEFECTO = 149      # Bogota D.C.
TIPO_SOLICITUD_OTRA = TipoSolicitud.OTRA

# Constantes del contrato con SGDEA: valen siempre lo mismo para este canal y
# no sustituyen ningun dato del correo, por lo que no se anotan en
# `suposiciones`.
TIPO_PRODUCTO_ARL = 15
# Solicitud. Es el unico tipo con el que este canal radica; los demas fijan
# terminos de respuesta distintos y su clasificacion no se decide aqui.
TIPO_PQRD_DEL_CANAL = 1
USUARIO_CREADOR = "Asignador IA"
# Estado y tramite con que entra el registro de trazabilidad. Los fija SGDEA;
# aqui solo se reproducen.
ESTADO_POR_ASIGNAR = "Por asignar"
TRAMITE = "PQRD"
MEDIO_RESPUESTA_CORREO = 1       # se responde por el mismo canal de entrada
LISTADO_CONDICIONES = "[1]"
DESCRIPCION_ACCESO_INFORMACION = 2

# Condiciones que ningun correo de PQRD declara y que este canal no distingue.
LGTBIQ_NO_DECLARADO = False
ACCESO_INFORMACION_PUBLICA = False


def _valor(
    hallazgos: Dict[str, Hallazgo],
    campo: str,
    defecto: Any = None,
    suposiciones: Optional[List[str]] = None,
) -> Any:
    h = hallazgos.get(campo)
    if h and h.valor not in (None, ""):
        return h.valor
    if suposiciones is not None and defecto not in (None, ""):
        suposiciones.append(f"{campo} no se resolvio: se envia {defecto!r}")
    return defecto


def _geografia(
    hallazgos: Dict[str, Hallazgo],
    afectado: Any,
    suposiciones: Optional[List[str]] = None,
) -> Tuple[int, int]:
    """
    Departamento y municipio, o los dos por defecto.

    Van juntos a proposito: un municipio de Caqueta con el departamento de
    Bogota describe un lugar que no existe. Si falta cualquiera de los dos se
    envian los dos por defecto y queda anotado, en vez de mezclar un dato leido
    con uno inventado.
    """
    def _entero(valor: Any) -> Optional[int]:
        try:
            return int(valor)
        except (TypeError, ValueError):
            return None

    departamento = _entero(_valor(hallazgos, "departamento_afectado")) or \
        _entero(getattr(afectado.departamento, "id_departamento", None))
    municipio = _entero(_valor(hallazgos, "municipio_afectado")) or \
        _entero(getattr(afectado.municipio, "id_municipio", None))

    if departamento and municipio:
        return departamento, municipio

    if suposiciones is not None:
        falta = ("departamento ni municipio"
                 if not departamento and not municipio
                 else "municipio" if departamento else "departamento")
        suposiciones.append(
            f"no se resolvio {falta} del afectado: se envian departamento "
            f"{DEPARTAMENTO_POR_DEFECTO} y municipio {MUNICIPIO_POR_DEFECTO}, "
            f"los dos por defecto, para no describir un lugar inexistente"
        )
    return DEPARTAMENTO_POR_DEFECTO, MUNICIPIO_POR_DEFECTO


# tipo_remitente == 3 significa "quien escribe ES el afectado". Solo en ese
# caso los dos documentos son legitimamente el mismo.
TIPO_REMITENTE_ES_EL_AFECTADO = 3


def _documento_del_remitente(
    hallazgos: Dict[str, Hallazgo],
    prefijo: str,
    suposiciones: Optional[List[str]] = None,
) -> Any:
    """
    Documento de quien escribe.

    Solo hereda el del afectado cuando `tipo_remitente` declara que son la
    misma persona. En cualquier otro caso devuelve vacio y lo anota.
    """
    propio = _valor(hallazgos, f"{prefijo}_remitente")
    if propio not in (None, ""):
        return propio

    del_afectado = _valor(hallazgos, f"{prefijo}_afectado")
    if _valor(hallazgos, "tipo_remitente") == TIPO_REMITENTE_ES_EL_AFECTADO:
        if suposiciones is not None and del_afectado not in (None, ""):
            suposiciones.append(
                f"{prefijo}_remitente no se resolvio: se usa el del afectado "
                "porque el remitente es la misma persona"
            )
        return del_afectado

    if suposiciones is not None:
        suposiciones.append(
            f"{prefijo}_remitente no se resolvio y NO se hereda del afectado: "
            "quien escribe es otra persona"
        )
    return ""


def _correo_del_afectado(
    email: EmailMessage,
    cuerpo_limpio: str,
    suposiciones: Optional[List[str]] = None,
) -> str:
    """
    Correo de contacto del afectado.

    Es el buzon del que llega el correo, salvo que ese buzon no sea de una
    persona: un buzon de Positiva o automatizado escrito aqui dirige a la propia
    entidad toda respuesta al ciudadano. En ese caso se busca el primer correo
    externo del hilo y, si no hay ninguno, el campo va vacio.
    """
    from utils.generic_filter import (
        extraer_correo_remitente_real,
        is_buzon_no_humano,
    )

    nombre = email.from_address.name if email.from_address else ""
    direccion = email.from_address.address if email.from_address else ""
    if not is_buzon_no_humano(nombre, direccion):
        return direccion

    externo = extraer_correo_remitente_real(direccion, cuerpo_limpio or "", nombre)
    if externo and not is_buzon_no_humano("", externo):
        if suposiciones is not None:
            suposiciones.append(
                f"correo del afectado: el correo llego de un buzon que no es de "
                f"una persona, se usa el primer externo del hilo ({externo})"
            )
        return externo

    if suposiciones is not None:
        suposiciones.append(
            "correo del afectado: el correo llego de un buzon que no es de una "
            "persona y el hilo no trae ninguno externo, se envia vacio"
        )
    return ""


def _tipo_remitente_por_defecto(hallazgos: Dict[str, Hallazgo]) -> int:
    """
    Tipo de remitente cuando ni el algoritmo ni el modelo lo resolvieron.

    El catalogo no tiene un valor para "no se sabe", asi que hay que afirmar
    algo, y de los tres "Tercero" es la afirmacion mas debil: dice que quien
    escribe no es la persona del caso, sin propagar su identidad. "Afectado"
    afirma que son la misma persona y ademas autoriza a `_documento_del_remitente`
    a copiarle su documento, con lo que un valor equivocado registra a un
    tercero con la cedula del trabajador.

    Cuando el correo SI muestra que quien escribe es el afectado, el campo se
    resuelve antes y este defecto no llega a aplicarse.
    """
    return TIPO_REMITENTE_TERCERO


def _codigo_tipo_documento(valor: Any) -> Optional[int]:
    """
    El tipo como codigo, o None si no lo hay.

    No inventa un tipo: devolver "cedula de ciudadania" ante un valor ausente
    tipifica como persona el NIT de una empresa. El llamador decide el defecto y
    lo anota.
    """
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def armar_payload(
    email: EmailMessage,
    hallazgos: Dict[str, Hallazgo],
    cuerpo_limpio: str = "",
    oficina_por_defecto: int = 15,
    suposiciones: Optional[List[str]] = None,
) -> RadicacionPayload:
    """
    Construye el payload a partir de los hallazgos ya validados.

    `suposiciones`, si se pasa, recibe una linea por cada campo que se rellena
    con un valor por defecto en vez de un dato extraido.
    """
    from datetime import datetime

    fecha = ""
    if email.received_datetime:
        try:
            fecha = datetime.fromisoformat(
                email.received_datetime.replace("Z", "+00:00")
            ).strftime("%d/%m/%Y %H:%M:%S")
        except ValueError:
            fecha = email.received_datetime

    nombre_afectado = _valor(hallazgos, "nombre_afectado", "")
    # El tipo se lee una sola vez, para que la suposicion se anote una sola vez.
    tipo_documento_afectado = _valor(
        hallazgos, "tipo_documento_afectado", TIPO_DOCUMENTO_POR_DEFECTO,
        suposiciones)
    # El documento se envia en la misma forma en que la compuerta lo valido:
    # solo digitos. Con puntos o con la sigla delante queda un segundo registro
    # de identidad para la misma persona.
    afectado = PersonaData.from_extracted_data(
        numero_documento=solo_digitos(
            _valor(hallazgos, "numero_documento_afectado", "")),
        tipo_documento=str(tipo_documento_afectado),
        nombre=nombre_afectado,
    )
    # Sin direccion se envia vacio: la del prestador o la del empleador
    # registraria a la persona donde no vive.
    afectado.direccion = _valor(hallazgos, "direccion_afectado", "")

    correo_afectado = _correo_del_afectado(email, cuerpo_limpio, suposiciones)
    departamento, municipio = _geografia(hallazgos, afectado, suposiciones)

    numero_remitente = solo_digitos(
        _documento_del_remitente(hallazgos, "numero_documento", suposiciones))
    tipo_documento_remitente = _codigo_tipo_documento(
        _documento_del_remitente(hallazgos, "tipo_documento", suposiciones))
    if tipo_documento_remitente is None:
        # Un tipo que no describe ningun documento, o que no se resolvio, se
        # envia como cedula de ciudadania y queda anotado.
        motivo = ("sin documento que describir" if not numero_remitente
                  else "no se resolvio")
        tipo_documento_remitente = TIPO_DOCUMENTO_POR_DEFECTO
        if suposiciones is not None:
            suposiciones.append(
                f"tipo_documento_remitente {motivo}: se envia "
                f"{TIPO_DOCUMENTO_POR_DEFECTO!r}"
            )

    codigo_afectado = _codigo_tipo_documento(tipo_documento_afectado)
    if codigo_afectado is None:
        codigo_afectado = TIPO_DOCUMENTO_POR_DEFECTO
        if suposiciones is not None:
            suposiciones.append(
                f"tipo_documento_afectado no es un codigo "
                f"({tipo_documento_afectado!r}): se envia "
                f"{TIPO_DOCUMENTO_POR_DEFECTO!r}"
            )

    return RadicacionPayload(
        datos_remitente_tipo_remitente_id=int(
            _valor(hallazgos, "tipo_remitente",
                   _tipo_remitente_por_defecto(hallazgos), suposiciones)
        ),
        datos_remitente_tipo_documento_id=tipo_documento_remitente,
        datos_remitente_numero_documento=numero_remitente,
        datos_remitente_nombre=_valor(hallazgos, "nombre_remitente", ""),
        datos_afectado_tipo_documento_id=codigo_afectado,
        datos_afectado_numero_documento=afectado.numero_documento,
        datos_afectado_nombre=afectado.nombre_completo or nombre_afectado,
        # SGDEA exige el campo. Se envia False porque nadie lo declara en un
        # correo de PQRD, no porque se sepa: es una condicion de la persona y
        # afirmarla sin evidencia es lo mismo que adivinar el sexo por el
        # nombre.
        datos_afectado_lgtbtq=LGTBIQ_NO_DECLARADO,
        # Si el correo no declara el sexo, se envia "no determinado". No se
        # deduce del nombre: eso escribiria un dato falso sobre una persona.
        datos_afectado_genero_id=int(
            _valor(hallazgos, "sexo_afectado", SEXO_POR_DEFECTO, suposiciones)
        ),
        datos_afectado_correo_electronico=correo_afectado,
        datos_afectado_telefono_celular=normalizar_telefono(
            _valor(hallazgos, "telefono_afectado", "")
        ),
        datos_afectado_pais_id_pais=afectado.pais.id_pais,
        datos_afectado_departamento_id_departamento=departamento,
        datos_afectado_municipio_id_municipio=municipio,
        datos_afectado_direccion=afectado.direccion,
        datos_afectado_medio_respuesta_id=MEDIO_RESPUESTA_CORREO,
        solicitud_tipo_pqrd_id=TIPO_PQRD_DEL_CANAL,
        solicitud_tipo_solicitud_id=int(
            _valor(hallazgos, "tipo_solicitud", TIPO_SOLICITUD_OTRA, suposiciones)
        ),
        solicitud_tipo_producto_id=TIPO_PRODUCTO_ARL,
        solicitud_acceso_informacion_publica=ACCESO_INFORMACION_PUBLICA,
        solicitud_descripcion_acceso_informacion_id=DESCRIPCION_ACCESO_INFORMACION,
        solicitud_observaciones=(
            f"Fecha: {fecha}  Asunto: {email.subject or ''} "
            f"Cuerpo: {(cuerpo_limpio or '')[:2000]}"
        ),
        solicitud_detalle="",
        usuario_creador=USUARIO_CREADOR,
        canal_radicacion=CANAL_EMAIL_IA,
        # Decide la cola a la que entra el caso.
        oficina=int(
            _valor(hallazgos, "oficina", oficina_por_defecto, suposiciones)
        ),
        detalle_afectado="",
        listado_condiciones=LISTADO_CONDICIONES,
    )


# Cuanto del fallo de una radicacion va al registro. La pagina de un bloqueo de
# Cloudflare ocupa decenas de miles de caracteres.
LARGO_DEL_FALLO_EN_EL_LOG = 500


def campos_para_el_log(payload: RadicacionPayload) -> Dict[str, Any]:
    """
    Los campos que van a SGDEA, como se registran.

    El cuerpo del correo se reemplaza por su largo: puede traer historia clinica,
    y SGDEA lo recibe aparte como observacion.
    """
    campos = dict(payload.to_form_data())
    cuerpo = campos.get("solicitudObservaciones") or ""
    campos["solicitudObservaciones"] = f"({len(cuerpo)} caracteres)"
    return campos


def _campos_vacios(campos: Dict[str, Any]) -> List[str]:
    """Los campos que se envian sin valor, para ver de un vistazo que falto."""
    return sorted(k for k, v in campos.items() if v in (None, ""))


# Nombre del documento que lleva el correo mismo. `RadicacionService` reconoce
# por este nombre el documento del correo y lo envia solo cuando la PQRD no
# trae ningun otro adjunto.
NOMBRE_DEL_CORREO = "correo.html"


def documento_del_correo(email: EmailMessage, cuerpo_limpio: str = "") -> bytes:
    """
    El correo como documento HTML, para radicarlo junto con la PQRD.

    Una PQRD sin adjuntos quedaria radicada sin ningun soporte: quien la
    atiende veria el numero de radicado y los campos, pero no lo que la persona
    escribio. Este documento es ese soporte.
    """
    de = ""
    if email.from_address is not None:
        nombre = getattr(email.from_address, "name", "") or ""
        correo = getattr(email.from_address, "address", "") or ""
        de = f"{nombre} <{correo}>".strip()

    cuerpo = cuerpo_limpio or getattr(email.body, "content", "") or ""
    filas = [
        ("Asunto", email.subject or ""),
        ("De", de),
        ("Fecha", email.received_datetime or email.sent_datetime or ""),
    ]
    encabezado = "".join(
        f"<tr><th align=\"left\">{html.escape(k)}</th>"
        f"<td>{html.escape(str(v))}</td></tr>"
        for k, v in filas if v
    )
    return (
        "<!doctype html><html lang=\"es\"><head><meta charset=\"utf-8\">"
        f"<title>{html.escape(email.subject or 'Correo')}</title></head><body>"
        f"<table>{encabezado}</table><hr>"
        f"<pre style=\"white-space:pre-wrap;font-family:inherit\">"
        f"{html.escape(cuerpo)}</pre>"
        "</body></html>"
    ).encode("utf-8")


@dataclass
class Radicador:
    """
    Envia la radicacion, reintentando segun la clase del fallo.

    `servicio` debe exponer `radicar_pqrd(payload, archivos)`. Se inyecta para
    poder probar sin red.

    El orquestador reutiliza una unica instancia para todo el lote, y el lote
    procesa varios correos a la vez: `bitacora` y `suposiciones` son atributos
    de instancia porque asi los lee el llamador (`self.radicador.bitacora = ...`
    antes de invocar, `self.radicador.suposiciones` despues), pero un correo
    los asigna antes de que otro, procesado en paralelo, haya terminado de
    leerlos. `radicar` los captura en variables locales al entrar y solo vuelve
    a escribir `self.suposiciones` como ultimo paso, sin ningun `await` de por
    medio, para que la asignacion de un correo no le cambie la bitacora ni las
    suposiciones a otro que esta a mitad de un reintento.
    """

    servicio: Any
    bitacora: Optional[Bitacora] = None
    dormir: Any = asyncio.sleep
    # Campos del ultimo armado que se enviaron con valor por defecto.
    suposiciones: List[str] = field(default_factory=list)

    async def radicar(
        self,
        email: EmailMessage,
        hallazgos: Dict[str, Hallazgo],
        archivos: Optional[List[Tuple[str, bytes]]] = None,
        cuerpo_limpio: str = "",
    ) -> str:
        """Devuelve el numero de radicado, o cadena vacia si no se logro."""
        # Se leen una sola vez, antes del primer `await`: entre que el
        # llamador asigna `self.bitacora` y esta linea no hay ninguna cesion
        # al event loop, asi que en este punto el valor es el de ESTE correo.
        # De aqui en adelante se usa la variable local, nunca `self.bitacora`,
        # para que otro correo que reasigne el atributo mientras este esta
        # esperando una respuesta no le cambie donde registra sus fallos.
        bitacora = self.bitacora
        suposiciones: List[str] = []
        try:
            payload = armar_payload(
                email, hallazgos, cuerpo_limpio, suposiciones=suposiciones
            )
            a_enviar = list(archivos or [])
            if not any(nombre == NOMBRE_DEL_CORREO for nombre, _ in a_enviar):
                a_enviar.append(
                    (NOMBRE_DEL_CORREO, documento_del_correo(email, cuerpo_limpio))
                )
            campos = campos_para_el_log(payload)
            detalle_del_envio = {
                **datos_del_correo(email),
                "campos_enviados": campos,
                "campos_vacios": _campos_vacios(campos),
                "por_defecto": list(suposiciones),
                "adjuntos": [nombre for nombre, _ in a_enviar],
            }
            intento = 0
            while True:
                intento += 1
                emitir(
                    f"[{detalle_del_envio['correo']}] radicacion enviada a SGDEA",
                    severidad=INFO, solo_json=("correo", "internet_message_id"),
                    en_su_linea=("remitente_correo", "asunto", "recibido",
                                 "campos_enviados", "campos_vacios", "por_defecto"),
                    intento=intento, **detalle_del_envio,
                )
                try:
                    respuesta = await self.servicio.radicar_pqrd(payload, a_enviar)
                    radicado = getattr(respuesta, "id_radicado", "") or ""
                    if not radicado:
                        # El backend acepto la peticion y no devolvio el numero. El
                        # registro pudo quedar creado, asi que devolver vacio en
                        # silencio manda el correo a una persona que lo radicaria
                        # otra vez.
                        #
                        # Se anotan los nombres de los campos que si llegaron. Sin
                        # eso no se puede distinguir que el backend no mandara el
                        # numero de que lo mandara con otro nombre, que son dos
                        # problemas en dos sitios distintos.
                        #
                        # Los campos van al principio del mensaje: la bitacora lo
                        # recorta a doscientos caracteres y al final se perderian.
                        claves = getattr(respuesta, "claves_recibidas", ()) or ()
                        raise RuntimeError(
                            "la radicacion se acepto sin numero de radicado. La "
                            "respuesta trajo "
                            + (", ".join(claves) if claves else "un cuerpo vacio")
                            + ". El registro pudo quedar creado en SGDEA y hay que "
                            "buscarlo antes de volver a radicar")
                    await self._dejar_traza(respuesta, radicado, payload, bitacora)
                    return radicado
                except Exception as e:
                    clase = clasificar(
                        estado=getattr(e, "status_code", None),
                        cuerpo=getattr(e, "cuerpo", "") or str(e),
                        excepcion=e,
                    )
                    emitir(
                        f"[{detalle_del_envio['correo']}] radicacion rechazada: "
                        f"{clase.codigo}",
                        severidad=AVISO,
                        solo_json=("correo", "clase", "internet_message_id"),
                        en_su_linea=("fallo", "remitente_correo", "asunto", "recibido",
                                     "campos_enviados", "campos_vacios", "por_defecto"),
                        intento=intento, clase=clase.codigo,
                        fallo=" ".join(str(getattr(e, "cuerpo", "") or e).split())[
                            :LARGO_DEL_FALLO_EN_EL_LOG],
                        **detalle_del_envio,
                    )
                    if bitacora is not None:
                        bitacora.registrar(
                            "RADICACION", str(e)[:200], excepcion=e,
                            cuerpo=getattr(e, "cuerpo", "") or str(e),
                            intento=intento,
                        )
                    # Radicar crea un radicado nuevo en cada llamada, asi que solo
                    # se repite cuando consta que la peticion no llego a ejecutarse.
                    # Lo demas se aplaza a la corrida siguiente, que vuelve a pasar
                    # por las capas de deduplicacion antes de radicar.
                    if not (debe_reintentar(clase, intento)
                            and puede_repetirse_una_escritura(clase)):
                        return ""
                    espera = espera_para(clase, intento)
                    if espera:
                        await self.dormir(espera)
        finally:
            # Ultimo paso, sin ningun `await` despues: es la unica forma de
            # que quien llama (que lee `self.radicador.suposiciones` justo
            # despues de este `await radicar(...)`) reciba las de ESTE correo
            # y no las de otro que comparte el mismo `Radicador` y termino de
            # escribir las suyas mientras este esperaba a SGDEA.
            self.suposiciones = suposiciones

    async def _dejar_traza(
        self, respuesta: Any, radicado: str, payload,
        bitacora: Optional[Bitacora],
    ) -> None:
        """
        Registra en SGDEA quien radico el caso y a que oficina entro.

        Es lo que despues permite auditar una PQRD sin mirar los logs del job:
        el radicado por si solo no dice que lo puso la IA ni por que cola
        entro.

        Un fallo aqui NO tumba la radicacion, que ya ocurrio y no se puede
        deshacer, pero queda en la bitacora: sin traza el caso existe y nadie
        sabe de donde salio. `bitacora` llega por parametro, capturada por
        `radicar` antes de empezar, para no leer `self.bitacora` despues de un
        `await` y arriesgarse a que sea la de otro correo.
        """
        crear = getattr(self.servicio, "crear_trazabilidad", None)
        if crear is None:
            return
        from business_rules import get_office_name
        from models.radicacion import TrazabilidadPayload

        oficina = get_office_name(payload.oficina)
        try:
            await crear(TrazabilidadPayload(
                proceso=f"PQRD{getattr(respuesta, 'id', '') or radicado}",
                secuencia=radicado,
                estado=ESTADO_POR_ASIGNAR,
                descripcion=f"Usuario: {USUARIO_CREADOR}  Descripcion: Se ha "
                            f"radicado una PQRD por medio del canal Email IA",
                comentario="Radicado",
                nombre=f"{USUARIO_CREADOR} - {oficina}",
                accion=f"{USUARIO_CREADOR} ha radicado la PQRD {radicado}",
                tramite=TRAMITE,
            ))
        except Exception as e:
            if bitacora is not None:
                bitacora.registrar(
                    "TRAZABILIDAD", str(e)[:200], excepcion=e,
                    cuerpo=getattr(e, "cuerpo", "") or str(e),
                )
