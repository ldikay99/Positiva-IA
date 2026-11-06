"""
Aviso que queda en el hilo del correo con el resultado del procesamiento.

Lleva el numero de radicado cuando la radicacion prospera, y el motivo cuando
no, de modo que la revision del buzon no dependa de consultar los registros de
la ejecucion.

El texto que sale al correo es DISTINTO del que va al informe interno. El
informe habla de modelo, compuerta, pipeline y taxonomia de errores; el correo
lo lee personal de la entidad, y ahi esos terminos no significan nada o
significan algo que no es. `MOTIVO_PARA_EL_CORREO` es la redaccion externa y
`telemetria.MOTIVOS_MANUAL` la interna; las dos cubren los mismos codigos y hay
una prueba que lo exige.
"""
from __future__ import annotations

import html

# Aviso de uso de inteligencia artificial. Acompaña a todo mensaje generado por
# el sistema.
PIE = (
    "Este mensaje fue generado total o parcialmente mediante Inteligencia "
    "Artificial como apoyo a los procesos de automatizacion. Aunque se han "
    "aplicado controles para garantizar su calidad, el contenido puede "
    "contener imprecisiones o errores. En caso de duda, prevalecera la "
    "informacion validada por la entidad."
)

_TITULOS = {
    "radicado": "Radicacion exitosa con IA",
    "duplicado": "La comunicacion ya se encontraba radicada",
    "reenviado": "Comunicacion remitida al area competente",
    "aplazado": "Radicacion pendiente de reintento",
    "manual": "No fue posible radicar automaticamente",
    "error": "No fue posible radicar automaticamente",
}

_QUE_SIGUE = {
    "radicado": "",
    "duplicado": "No se genera un nuevo radicado.",
    "reenviado": "La comunicacion no corresponde a una PQRD.",
    "aplazado": "El sistema reintentara la radicacion automaticamente.",
    "manual": "La comunicacion queda disponible para gestion manual.",
    "error": "La comunicacion queda disponible para gestion manual.",
}

# Redaccion externa de cada motivo. Describe QUE falto, no que componente
# fallo: quien lee el correo no opera el sistema.
MOTIVO_PARA_EL_CORREO = {
    "SIN_NOMBRE_REMITENTE":
        "No fue posible identificar a la persona que remite la comunicacion.",
    "SIN_NOMBRE_AFECTADO":
        "No fue posible identificar a la persona afectada.",
    "SIN_DOCUMENTO":
        "No fue posible identificar el documento de identidad.",
    "DOCUMENTO_AMBIGUO":
        "La comunicacion contiene varios numeros de identificacion y no fue "
        "posible establecer cual corresponde a la persona afectada.",
    "DOCUMENTO_DE_OTRA_PERSONA":
        "El numero de identificacion y el nombre de la persona afectada no "
        "corresponden entre si.",
    "EMPRESA_CON_DOCUMENTO_DE_PERSONA":
        "El nombre corresponde a una empresa y el documento a una persona "
        "natural.",
    "SIN_TIPO_SOLICITUD":
        "No fue posible clasificar el tipo de solicitud.",
    "SOLO_EMPRESA_SIN_AFECTADO":
        "La comunicacion identifica una empresa pero no una persona afectada.",
    "ADJUNTO_PROTEGIDO":
        "Uno de los documentos adjuntos esta protegido con contraseña.",
    "ADJUNTO_ILEGIBLE":
        "No fue posible leer uno de los documentos adjuntos.",
    "ADJUNTO_NO_LEIDO":
        "Uno de los documentos adjuntos esta en un formato que no se pudo leer.",
    "SIN_OCR":
        "No fue posible extraer el texto de un documento adjunto.",
    "VALIDACION_RECHAZO":
        "Los datos obtenidos no superaron las validaciones de consistencia.",
    "PETICION_MAL_FORMADA":
        "La comunicacion no pudo registrarse en el sistema de radicacion.",
    "RADICACION_SIN_CONFIRMAR":
        "El sistema de radicacion acepto la comunicacion sin confirmar su "
        "numero. Verificar si ya quedo registrada antes de radicarla de nuevo.",
    "INYECCION_DETECTADA":
        "El contenido de la comunicacion requiere revision de un funcionario.",
    "VOLUMEN_EXCESIVO":
        "La comunicacion trae mas documentos o paginas de los que se tramitan "
        "de forma automatica y requiere revision de un funcionario.",
    "FACTURA_ELECTRONICA":
        "La comunicacion corresponde a una factura electronica y no a una PQRD.",
    "REMITENTE_EXCLUIDO":
        "La comunicacion la escribe un buzon que no radica por este canal.",
    "REMITENTE_NO_IDENTIFICABLE":
        "No fue posible identificar un remitente externo en la conversacion.",
    "CLOUDFLARE_403":
        "El sistema de radicacion no estuvo disponible durante el "
        "procesamiento.",
    "SGDEA_CAIDO":
        "El sistema de radicacion no estuvo disponible durante el "
        "procesamiento.",
    "MODELO_NO_RESPONDIO":
        "El servicio de procesamiento automatico no estuvo disponible.",
    "MODELO_SIN_PERMISO":
        "El servicio de procesamiento automatico no estuvo disponible.",
    "RECLAMO_AGOTADO":
        "El procesamiento automatico no pudo completarse.",
    "TIEMPO_DE_CORRIDA_AGOTADO":
        "El procesamiento automatico no alcanzo a completarse.",
    "ERROR_INESPERADO":
        "El procesamiento automatico no pudo completarse.",
}

# Redaccion de respaldo. Un codigo nuevo sin redaccion externa saldria como
# codigo, y ahi es preferible una frase generica.
_SIN_REDACCION = "El procesamiento automatico no pudo completarse."


def _motivo_legible(motivo: str) -> str:
    codigo = (motivo or "").strip()
    if not codigo:
        return ""
    return MOTIVO_PARA_EL_CORREO.get(codigo, _SIN_REDACCION)


def _p(texto: str, estilo: str = "") -> str:
    atributo = f' style="{estilo}"' if estilo else ""
    return f"<p{atributo}>{html.escape(texto)}</p>"


def encabezado_de_reenvio(area: str, evidencia: str = "") -> str:
    """
    Cabecera del correo que se deriva a otra area.

    El area va como parametro y no fija en el texto: con una sola cabecera para
    los dos destinos, lo que llega a Notificaciones Judiciales anuncia que se
    derivo a Medicina Laboral.
    """
    partes = [
        '<div style="background-color:#1a3a5c;color:#fff;padding:12px 20px;'
        'border-radius:6px;font-family:Arial,sans-serif;margin-bottom:18px;'
        'border-left:5px solid #f0a500;">'
        '<p style="margin:0;font-weight:bold;font-size:15px;">'
        'REENVIO ELECTRONICO AUTOMATICO CON IA</p>'
        f'<p style="margin:6px 0 0;">Comunicacion derivada automaticamente al '
        f'area de {html.escape(area or "destino")}.</p>'
    ]
    if evidencia:
        partes.append(
            '<p style="margin:6px 0 0;font-size:12px;">'
            + html.escape(evidencia) + "</p>"
        )
    partes.append("</div>")
    return "".join(partes)


def redactar(
    accion: str,
    radicado: str = "",
    motivo: str = "",
    detalle: str = "",
    oficina: str = "",
) -> str:
    """
    Cuerpo del aviso, en HTML.

    `detalle` describe este caso en concreto —el area a la que se remitio, los
    datos que faltaron— y acompaña al motivo, que es generico.
    """
    titulo = _TITULOS.get(accion, _TITULOS["manual"])
    color = "#0b7a3b" if accion in ("radicado", "duplicado") else "#8a6100"

    partes = [
        f'<p style="font-weight:bold;color:{color};font-size:15px;">'
        f'{html.escape(titulo)}</p>'
    ]

    if accion in ("radicado", "duplicado") and radicado:
        cuerpo = f"Numero de radicado: {radicado}."
        if oficina:
            cuerpo += f" Oficina destino: {oficina}."
        partes.append(_p(cuerpo))
    else:
        legible = _motivo_legible(motivo)
        if legible:
            partes.append(_p(legible))

    if detalle:
        partes.append(_p(detalle))

    siguiente = _QUE_SIGUE.get(accion, "")
    if siguiente:
        partes.append(_p(siguiente))

    partes.append(
        '<p style="font-size:11px;color:#666;margin-top:14px;">'
        + html.escape(PIE) + "</p>"
    )
    return "".join(partes)
