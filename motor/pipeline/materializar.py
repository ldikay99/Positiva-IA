"""
Materializacion: de un correo con adjuntos a un workspace en Markdown.

Une el workspace y los conversores. Es el paso 3 del pipeline y el que hace
auditable todo lo que viene despues: a partir de aqui, cada dato extraido puede
señalar el archivo y la linea de donde salio.
"""
from __future__ import annotations

import re
from typing import Iterable, List, Optional, Sequence, Tuple

from pipeline import cache_conversion
from pipeline.conversion import (
    Conversion,
    PresupuestoDeOcr,
    detectar_tipo,
    devolver_memoria,
    expandir_zip,
    normalizar,
)
from pipeline.workspace import (
    ESTADO_DEMASIADO_GRANDE,
    ESTADO_ERROR,
    ESTADO_NO_SOPORTADO,
    ESTADO_OK,
    ESTADO_VACIO,
    EntradaManifiesto,
    Workspace,
    sha256_de,
)

# Adjuntos por encima de este tamaño no se convierten: el costo de OCR no se
# justifica y el limite protege la memoria del job (8 GiB, compartidos).
LIMITE_BYTES_ADJUNTO = 60 * 1024 * 1024

# Nombres tipicos de imagenes de firma de Outlook, que no aportan informacion.
_RUIDO = ("outlook", "signature", "firma", "logo", "spacer", "pixel")

# Las imagenes que Outlook incrusta en una firma se numeran `image001`,
# `image002`, y siguen: un hilo largo llega a `image0012`. Nombrarlas una por
# una dejaba fuera todo lo que pasara de la tercera.
_IMAGEN_DE_FIRMA = re.compile(r"\bimage\d{3,}\b", re.IGNORECASE)


def es_ruido(nombre: str) -> bool:
    """
    True si el adjunto es decorativo: firma, logo o espaciador.

    Estos no llegan al conversor, asi que no gastan OCR ni cuentan para el
    volumen del correo.
    """
    n = (nombre or "").lower()
    if any(p in n for p in _RUIDO):
        return True
    return bool(_IMAGEN_DE_FIRMA.search(n))


def cuerpo_a_markdown(asunto: str, cuerpo_limpio: str) -> str:
    """Cuerpo del correo actual como Markdown con su asunto como titulo."""
    cuerpo = normalizar(cuerpo_limpio) or "_(sin contenido)_"
    titulo = normalizar(asunto) or "(sin asunto)"
    return f"# {titulo}\n\n{cuerpo}\n"


def hilo_a_markdown(mensajes: Sequence[Tuple[str, str, str]]) -> str:
    """
    Hilo como Markdown, con cada mensaje atribuido a su remitente.

    La atribucion es la diferencia entre poder y no poder decidir de quien es
    una firma: el pipeline actual concatena todo el hilo en un solo texto, y
    por eso la firma de un mensaje anterior se confunde con la del actual.
    """
    if not mensajes:
        return ""
    partes: List[str] = ["# Mensajes anteriores del hilo\n"]
    for i, (remitente, fecha, cuerpo) in enumerate(mensajes, 1):
        partes.append(
            f"## Mensaje {i}\n\n"
            f"- **De:** {normalizar(remitente) or '(desconocido)'}\n"
            f"- **Fecha:** {normalizar(fecha) or '(sin fecha)'}\n\n"
            f"{normalizar(cuerpo) or '_(sin contenido)_'}\n"
        )
    return "\n".join(partes)


def claves_candidatas_de(texto: str) -> List[str]:
    """
    Numeros que podrian ser la contraseña de un PDF protegido.

    Los ciudadanos protegen el adjunto con su propio numero de documento y lo
    escriben en el cuerpo del correo. Probar esos numeros recupera adjuntos que
    de otro modo se pierden completos.
    """
    import re as _re

    vistos: List[str] = []
    for m in _re.finditer(r"\b\d{6,12}\b", texto or ""):
        n = m.group(0)
        if n not in vistos:
            vistos.append(n)
    return vistos[:12]  # tope: probar claves no debe volverse fuerza bruta


def claves_de_los_adjuntos(ws: Workspace) -> List[str]:
    """
    Documentos de identidad que aparecen en los adjuntos ya convertidos, los
    que llevan marca de documento primero. Son las claves que se prueban en un
    PDF protegido que no abrio con lo del correo.
    """
    from utils.documento_candidatos import extraer_candidatos_documento

    try:
        texto = ws.texto_completo()
    except Exception:
        return []
    candidatos = extraer_candidatos_documento(texto)
    marcados = [c["numero"] for c in candidatos
                if c.get("marca_pegada") or c.get("tiene_cc") or c.get("tiene_nit")]
    resto = [c["numero"] for c in candidatos if c["numero"] not in marcados]
    vistos: List[str] = []
    for n in marcados + resto:
        if n not in vistos:
            vistos.append(n)
    return vistos[:12]  # tope: probar claves no debe volverse fuerza bruta


def materializar_adjuntos(
    ws: Workspace,
    adjuntos: Iterable[Tuple[str, bytes]],
    expandir_comprimidos: bool = True,
    claves_candidatas: Optional[List[str]] = None,
) -> int:
    """
    Convierte cada adjunto a Markdown y lo registra en el manifiesto.

    Devuelve la cantidad convertida con exito. Los fallos no interrumpen: cada
    uno queda anotado con su motivo, que es lo que permite despues saber por
    que un dato no aparecio en lugar de suponerlo.
    """
    indice = 0
    convertidos = 0
    pendientes: List[Tuple[str, bytes]] = list(adjuntos)
    vistos_hash: set[str] = set()
    # UN presupuesto de OCR para todo el correo. El tope por documento no acota
    # el correo: un correo con dieciseis escaneos lo multiplica por dieciseis y
    # se lleva la ventana entre corridas.
    presupuesto = PresupuestoDeOcr()
    quedaron_sin_convertir: List[str] = []
    # PDF con contraseña que no abrieron las claves del correo. Se reintentan
    # al final con los documentos que aparecieron en los OTROS adjuntos: el
    # ciudadano protege el resumen de su historia con su cedula, y la cedula
    # esta en la orden medica que mando al lado, no en el cuerpo.
    protegidos: List[Tuple[str, bytes]] = []
    reintento_hecho = False

    while pendientes or (protegidos and not reintento_hecho):
        if not pendientes:
            reintento_hecho = True
            # Aunque no haya claves nuevas se vuelven a pasar: asi quedan en el
            # manifiesto con su error y no desaparecen sin rastro.
            nuevas = [c for c in claves_de_los_adjuntos(ws)
                      if c not in (claves_candidatas or [])]
            claves_candidatas = list(claves_candidatas or []) + nuevas
            for _n, d in protegidos:
                vistos_hash.discard(sha256_de(d))
            pendientes, protegidos = protegidos, []
        nombre, datos = pendientes.pop(0)
        indice += 1

        if es_ruido(nombre):
            ws.manifiesto.agregar(EntradaManifiesto(
                nombre_original=nombre, ruta_md="",
                bytes_originales=len(datos or b""),
                estado=ESTADO_VACIO,
                detalle="descartado: adjunto decorativo (firma, logo o espaciador)",
            ))
            continue

        if not datos:
            ws.manifiesto.agregar(EntradaManifiesto(
                nombre_original=nombre, ruta_md="", estado=ESTADO_VACIO,
                detalle="el adjunto llego vacio",
            ))
            continue

        digest = sha256_de(datos)
        if digest in vistos_hash:
            # Mismo contenido con otro nombre: el hilo repite adjuntos en cada
            # reenvio, y convertirlos de nuevo es gasto puro.
            ws.manifiesto.agregar(EntradaManifiesto(
                nombre_original=nombre, ruta_md="", sha256=digest,
                bytes_originales=len(datos), estado=ESTADO_VACIO,
                detalle="descartado: contenido duplicado de otro adjunto",
            ))
            continue
        vistos_hash.add(digest)

        if len(datos) > LIMITE_BYTES_ADJUNTO:
            ws.manifiesto.agregar(EntradaManifiesto(
                nombre_original=nombre, ruta_md="", sha256=digest,
                bytes_originales=len(datos), estado=ESTADO_DEMASIADO_GRANDE,
                detalle=f"{len(datos) / 1048576:.1f} MB excede el limite de "
                        f"{LIMITE_BYTES_ADJUNTO / 1048576:.0f} MB",
            ))
            ws.manifiesto.avisar(f"adjunto omitido por tamaño: {nombre}")
            continue

        tipo = detectar_tipo(datos, nombre)

        if tipo == "zip" and expandir_comprimidos:
            descartados: List[str] = []
            internos = expandir_zip(datos, descartados=descartados)
            detalle = f"comprimido expandido en {len(internos)} archivos"
            if descartados:
                # Un ZIP suele traer los soportes del caso. Si algo no sale,
                # tiene que constar: si no, el correo parece venir sin
                # documentos y se manda a revision manual con el motivo malo.
                detalle += "; " + "; ".join(descartados)
                for d in descartados:
                    ws.manifiesto.avisar(f"'{nombre}': {d}")
            ws.manifiesto.agregar(EntradaManifiesto(
                nombre_original=nombre, ruta_md="", tipo_real="zip",
                sha256=digest, bytes_originales=len(datos), estado=ESTADO_VACIO,
                detalle=detalle,
            ))
            # Se agregan al final para no recursar: un ZIP dentro de un ZIP
            # no se vuelve a expandir, y eso es deliberado.
            pendientes.extend(internos)
            continue

        # El presupuesto NO se comprueba aqui: un PDF con texto nativo no gasta
        # OCR y hay que convertirlo igual. Lo aplica el conversor, que sabe si
        # una pagina necesita reconocerse o si el texto ya estaba.
        # Por la cache de contenido: el mismo archivo no se lee dos veces.
        conv: Conversion = cache_conversion.convertir(
            datos, nombre, claves_candidatas, presupuesto=presupuesto)
        devolver_memoria()
        if presupuesto.motivo() in (conv.aviso or ""):
            quedaron_sin_convertir.append(nombre)
        entrada = EntradaManifiesto(
            nombre_original=nombre,
            ruta_md="",
            tipo_declarado=nombre.lower().rsplit(".", 1)[-1] if "." in nombre else "",
            tipo_real=conv.tipo_real or tipo,
            bytes_originales=len(datos),
            sha256=digest,
            ocr_aplicado=conv.ocr_aplicado,
        )

        if (conv.error and "contraseña de usuario" in conv.error
                and not reintento_hecho):
            protegidos.append((nombre, datos))
            continue

        if conv.error:
            entrada.estado = (
                ESTADO_NO_SOPORTADO if "no soportado" in conv.error else ESTADO_ERROR
            )
            entrada.detalle = conv.error
            ws.manifiesto.agregar(entrada)
            ws.manifiesto.avisar(f"no se pudo convertir '{nombre}': {conv.error}")
            continue

        if conv.ilegible:
            # El archivo se abrio pero ninguna pagina dio texto: es un escaneo
            # que el OCR no pudo leer. Se marca como fallo, no como convertido,
            # para que el motivo de revision manual diga ADJUNTO_ILEGIBLE en
            # vez de SIN_DOCUMENTO. Son problemas distintos y se atacan en
            # sitios distintos.
            entrada.estado = ESTADO_ERROR
            entrada.detalle = conv.aviso or "ninguna pagina dio texto"
            ws.manifiesto.agregar(entrada)
            # El nombre del archivo va DETRAS de la causa: delante, el informe
            # agrupa por nombre y saca una clase por adjunto.
            ws.manifiesto.avisar(
                f"adjunto ilegible: '{nombre}' — {entrada.detalle}")
            continue

        if conv.vacio:
            entrada.estado = ESTADO_VACIO
            entrada.detalle = "la conversion no produjo texto"
            ws.manifiesto.agregar(entrada)
            continue

        ruta = ws.escribir_adjunto(indice, nombre, conv.markdown)
        entrada.ruta_md = str(ruta.relative_to(ws.raiz))
        entrada.caracteres_md = len(conv.markdown)
        entrada.estado = ESTADO_OK
        if conv.truncado:
            entrada.detalle = "contenido truncado por exceder el limite"
            ws.manifiesto.avisar(f"adjunto truncado por tamaño: '{nombre}'")
        if conv.aviso:
            entrada.detalle = (entrada.detalle + "; " if entrada.detalle else "") + conv.aviso
            # Un aviso por causa: unirlas dejaria agrupable solo la primera y
            # las demas enterradas en el detalle.
            for causa in conv.aviso.split("; "):
                ws.manifiesto.avisar(f"{causa} (adjunto '{nombre}')")
        ws.manifiesto.agregar(entrada)
        convertidos += 1

    if quedaron_sin_convertir:
        # Un solo aviso con la cuenta: uno por adjunto sacaria del informe a
        # todo lo demas cuando un correo trae dieciseis.
        ws.manifiesto.avisar(
            f"{presupuesto.motivo()}: quedaron {len(quedaron_sin_convertir)} "
            f"adjunto(s) sin convertir, p.ej. "
            f"'{quedaron_sin_convertir[0]}'")

    return convertidos


def materializar(
    id_correo: str,
    asunto: str,
    cuerpo_limpio: str,
    remitente_nombre: str = "",
    remitente_correo: str = "",
    hilo: Optional[Sequence[Tuple[str, str, str]]] = None,
    adjuntos: Optional[Iterable[Tuple[str, bytes]]] = None,
    base: Optional[str] = None,
    temporal: bool = True,
) -> Workspace:
    """
    Materializa un correo completo en un workspace.

    Lo devuelve abierto, para que los pasos siguientes lo lean. Cerrarlo es
    responsabilidad del llamador, o del gestor de contexto.
    """
    ws = Workspace.crear(id_correo, base=base, temporal=temporal)
    ws.manifiesto.asunto = asunto
    ws.manifiesto.remitente_nombre = remitente_nombre
    ws.manifiesto.remitente_correo = remitente_correo
    ws.escribir_correo(cuerpo_a_markdown(asunto, cuerpo_limpio))
    if hilo:
        ws.escribir_hilo(hilo_a_markdown(hilo))
    if adjuntos:
        # Las claves candidatas salen del asunto y del cuerpo, que es donde el
        # ciudadano escribe el documento con el que protegio el PDF.
        claves = claves_candidatas_de(f"{asunto}\n{cuerpo_limpio}")
        materializar_adjuntos(ws, adjuntos, claves_candidatas=claves)
    ws.escribir_manifiesto()
    return ws
