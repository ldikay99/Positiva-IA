"""
Herramientas expuestas al agente. Todas de solo lectura o calculo puro.

Los errores estan redactados para que el modelo pueda corregirse solo, y las
respuestas estan acotadas para que no pueda pedir el documento completo.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from models.enums import (
    CODIGO_TO_TIPO_DOCUMENTO,
    TIPO_DOCUMENTO_CODIGO,
    TipoDocumento,
)
from pipeline.saneamiento import sanear_fragmento
from pipeline.workspace import Workspace
from utils.tipos_documento import (
    LONGITUD_POR_TIPO,
    MAX_DIGITOS,
    MIN_DIGITOS,
    TIPOS_ALFANUMERICOS,
    es_pasaporte,
    longitud_valida,
    rango_aceptable,
    tipos_compatibles,
)

# Nombre legible de cada tipo. La sigla sola ("PT", "NP") no le dice nada al
# modelo, que tiene que elegir entre ellos leyendo el correo.
_NOMBRE_TIPO_DOCUMENTO: Dict[int, str] = {
    int(TipoDocumento.CC): "Cedula de ciudadania",
    int(TipoDocumento.CE): "Cedula de extranjeria",
    int(TipoDocumento.CD): "Carne diplomatico",
    int(TipoDocumento.PPT): "Permiso por proteccion temporal",
    int(TipoDocumento.PE): "Permiso especial de permanencia",
    int(TipoDocumento.TI): "Tarjeta de identidad",
    int(TipoDocumento.NP): "Registro civil o NUIP",
    int(TipoDocumento.SE): "Sociedad extranjera sin NIT",
    int(TipoDocumento.FI): "Fideicomiso",
    int(TipoDocumento.NI): "NIT",
    int(TipoDocumento.PA): "Pasaporte",
    int(TipoDocumento.NA): "Ninguno",
}

# Topes por llamada. Existen para que el agente no pueda deshacer el diseño
# pidiendo todo de una vez.
MAX_LINEAS_POR_LECTURA = 200
MAX_RESULTADOS_BUSQUEDA = 25
MAX_CARACTERES_RESPUESTA = 12_000


class ErrorHerramienta(Exception):
    """
    Error que el agente PUEDE corregir por si mismo.

    Se distingue de una excepcion interna: el mensaje esta redactado para que
    el modelo entienda que hizo mal y reintente con otros argumentos.
    """


@dataclass
class Resultado:
    """Lo que devuelve una herramienta, mas la contabilidad de su uso."""

    ok: bool
    datos: Any = None
    error: str = ""
    truncado: bool = False

    def a_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"ok": self.ok}
        if self.ok:
            d["datos"] = self.datos
            if self.truncado:
                d["aviso"] = (
                    "la respuesta se acorto por exceder el limite; "
                    "pide un rango mas pequeño o busca antes de leer"
                )
        else:
            d["error"] = self.error
        return d


def _acotar(texto: str) -> tuple[str, bool]:
    if len(texto) <= MAX_CARACTERES_RESPUESTA:
        return texto, False
    return texto[:MAX_CARACTERES_RESPUESTA], True


# ── Catalogos: datos de negocio que el agente consulta en vez de adivinar ──

# tipo_documento se deriva de models.enums.
CATALOGOS: Dict[str, Dict[Any, str]] = {
    "tipo_documento": {
        int(t): _NOMBRE_TIPO_DOCUMENTO.get(int(t), TIPO_DOCUMENTO_CODIGO[int(t)])
        for t in TipoDocumento
    },
    # Estos dos no tienen enum: son el contrato de SGDEA para este canal y no
    # aparecen en `models.enums`.
    "tipo_remitente": {
        1: "Apoderado o familiar del afectado",
        2: "Tercero (empresa u otra entidad)",
        3: "El afectado mismo",
    },
    "sexo": {
        1: "Masculino",
        2: "Femenino",
        3: "Otro o no determinado",
    },
}


def _cargar_tipo_solicitud() -> None:
    """Agrega el catalogo de tipos de solicitud desde business_rules."""
    try:
        from business_rules.office_mapping import TIPO_SOLICITUD_NOMBRES
    except ImportError:
        return
    CATALOGOS["tipo_solicitud"] = dict(TIPO_SOLICITUD_NOMBRES)


_cargar_tipo_solicitud()


@dataclass
class Contexto:
    """
    Lo que las herramientas necesitan para funcionar.

    Se pasa explicitamente en lugar de usar estado global: asi las pruebas
    construyen un contexto propio y no hay riesgo de que una corrida
    contamine a otra.
    """

    workspace: Workspace
    catalogos: Dict[str, Dict[Any, str]] = field(default_factory=lambda: dict(CATALOGOS))
    # Consulta opcional a SGDEA. Es una funcion para que las pruebas la
    # sustituyan sin tocar la red.
    consultar_persona: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None
    # Hallazgos que el agente reporta. El pipeline los lee despues; la
    # herramienta solo acumula, no actua.
    hallazgos: List[Dict[str, Any]] = field(default_factory=list)
    # Lo que la capa determinista ya resolvio. El agente no lo reporta —no se
    # le pide— pero forma parte de lo que se va a enviar, asi que el revisor
    # tiene que verlo.
    resueltos: Dict[str, Any] = field(default_factory=dict)
    # Corre al revisor sobre unos valores y devuelve su veredicto. Se inyecta
    # con el modelo ya enlazado, para que la herramienta no sepa de modelos.
    revisar: Optional[Callable[[Dict[str, Any]], Any]] = None


# ── Herramientas de lectura ──

def listar_documentos(ctx: Contexto) -> Resultado:
    """
    Inventario del workspace, SIN contenido.

    El agente ve que documentos hay y decide cual leer.
    """
    return Resultado(ok=True, datos=ctx.workspace.listar())


def leer_documento(
    ctx: Contexto, documento: str, desde: int = 0, lineas: int = 60,
    pagina: Optional[int] = None,
) -> Resultado:
    """
    Lee un fragmento de un documento por rango de lineas, o una pagina
    entera (`pagina`) de un adjunto con secciones `## Pagina N`.
    """
    if not documento:
        return Resultado(ok=False, error="falta el argumento 'documento'")
    if pagina not in (None, ""):
        return _leer_pagina(ctx, documento, pagina)
    if lineas <= 0:
        return Resultado(ok=False, error="'lineas' debe ser mayor que cero")
    if lineas > MAX_LINEAS_POR_LECTURA:
        return Resultado(
            ok=False,
            error=f"'lineas' no puede exceder {MAX_LINEAS_POR_LECTURA}; "
                  f"lee por partes o usa buscar_en_documentos para ubicarte",
        )
    if desde < 0:
        return Resultado(ok=False, error="'desde' no puede ser negativo")
    try:
        texto = ctx.workspace.leer(documento, desde=desde, hasta=desde + lineas)
    except FileNotFoundError:
        return _no_existe(ctx, documento)
    return _contenido_leido(documento, texto, {"desde": desde})


def _no_existe(ctx: Contexto, documento: str) -> Resultado:
    disponibles = [d["id"] for d in ctx.workspace.listar()]
    return Resultado(
        ok=False,
        error=f"no existe el documento '{documento}'. "
              f"Disponibles: {', '.join(disponibles) or '(ninguno)'}",
    )


def _leer_pagina(ctx: Contexto, documento: str, pagina: Any) -> Resultado:
    try:
        numero = int(pagina)
    except (TypeError, ValueError):
        return Resultado(ok=False, error="'pagina' debe ser un numero entero")
    try:
        texto, inicio = ctx.workspace.leer_pagina(documento, numero)
    except FileNotFoundError:
        return _no_existe(ctx, documento)
    except ValueError as e:
        return Resultado(ok=False, error=str(e))
    return _contenido_leido(documento, texto, {"pagina": numero, "desde": inicio})


def _contenido_leido(documento: str, texto: str,
                     ubicacion: Dict[str, Any]) -> Resultado:
    acotado, truncado = _acotar(texto)
    # El contenido de un adjunto viene de un tercero desconocido: se anota lo
    # sospechoso y se envuelve en el bloque delimitado que el prompt del sistema
    # declara, antes de que llegue al modelo.
    saneado = sanear_fragmento(acotado, documento)
    return Resultado(
        ok=True,
        datos={
            "documento": documento,
            **ubicacion,
            "contenido": saneado.markdown,
            "sospechas": [
                {"categoria": s.categoria, "severidad": s.severidad.value,
                 "linea": s.linea, "explicacion": s.explicacion}
                for s in saneado.sospechas
            ],
        },
        truncado=truncado,
    )


# Dentro del grupo cuenta cualquier cuantificador: `(a?)*` explota igual que
# `(a+)+`.
_CUANTIFICADORES = "*+?{"


def _repeticion_abierta(patron: str, i: int) -> bool:
    """
    Si el cuantificador que empieza en `i` puede repetir sin tope.

    Solo los abiertos (`*`, `+`, `{n,}`) causan la explosion combinatoria: `?`
    prueba dos caminos y `{2,4}` prueba tres.
    """
    ch = patron[i: i + 1]
    if not ch:
        return False  # `"" in "*+"` es True en Python
    if ch in "*+":
        return True
    if ch != "{":
        return False
    cierre = patron.find("}", i)
    if cierre < 0:
        return False
    # `{n,}` no tiene tope; `{n}` y `{n,m}` si.
    return patron[i + 1: cierre].endswith(",")


def _cuantificador_anidado(patron: str) -> str:
    """
    Detecta un cuantificador aplicado a un grupo que ya lleva otro dentro.

    Es la forma de `(a+)+`: contra una linea larga, el motor de expresiones
    regulares explora un numero exponencial de combinaciones y el proceso se
    queda colgado. El patron lo elige el modelo, y el modelo lee documentos que
    vienen de fuera: un adjunto puede sugerirle uno asi.

    Tambien cubre `(a|a)+`: alternativas que se solapan bajo un cuantificador
    producen la misma explosion sin necesidad de un cuantificador interno.

    Es una heuristica, no una prueba: cubre las formas que causan el problema
    en la practica. Devuelve el motivo, o cadena vacia si es aceptable.
    """
    # Cada escape se sustituye por UN caracter neutro, no se borra: borrarlo
    # dejaria el cuantificador que lo acompaña pegado al parentesis anterior y
    # `\s*` pareceria aplicarse al grupo.
    limpio = re.sub(r"\\.", "\x00", patron)
    # El prefijo de un grupo no capturante o de una asercion lleva `?`, que no
    # es un cuantificador.
    limpio = re.sub(r"\(\?[:=!<][a-zA-Z]*", "(", limpio)

    pila = []
    for i, ch in enumerate(limpio):
        if ch == "(":
            pila.append(i)
        elif ch == ")" and pila:
            inicio = pila.pop()
            if not _repeticion_abierta(limpio, i + 1):
                continue
            cuerpo = limpio[inicio + 1: i]
            if any(c in _CUANTIFICADORES for c in cuerpo):
                return (
                    f"el patron '{patron[:60]}' tiene un cuantificador sobre "
                    f"un grupo que ya lleva otro: contra una linea larga "
                    f"puede colgar el proceso."
                )
            if "|" in cuerpo:
                return (
                    f"el patron '{patron[:60]}' repite un grupo con "
                    f"alternativas: si dos coinciden con lo mismo, contra una "
                    f"linea larga puede colgar el proceso."
                )
    return ""


def buscar_en_documentos(
    ctx: Contexto, patron: str, contexto_lineas: int = 1, documento: str = "",
) -> Resultado:
    """
    Busca un patron en todos los documentos y devuelve donde aparece.

    Permite ubicar un dato sin traerse el documento entero.
    """
    if not patron:
        return Resultado(ok=False, error="falta el argumento 'patron'")
    if len(patron) > 200:
        return Resultado(ok=False, error="el patron es demasiado largo")
    peligro = _cuantificador_anidado(patron)
    if peligro:
        return Resultado(
            ok=False,
            error=f"{peligro} Escribe el texto literal o una alternativa "
                  f"simple: para ubicar un dato no hace falta mas.",
        )
    try:
        hallazgos = ctx.workspace.buscar(patron, contexto=max(0, contexto_lineas),
                                         documento=str(documento or ""))
    except ValueError as e:
        return Resultado(
            ok=False,
            error=f"{e}. Usa una expresion regular valida, o texto literal.",
        )
    except FileNotFoundError:
        return _no_existe(ctx, str(documento))
    total = len(hallazgos)
    recortados = hallazgos[:MAX_RESULTADOS_BUSQUEDA]
    # Lo que devuelve la busqueda tambien es contenido del documento: entra al
    # modelo igual que una lectura y necesita el mismo tratamiento.
    for h in recortados:
        h["contexto"] = sanear_fragmento(
            h.get("contexto", ""), h.get("documento", "?")).markdown
    return Resultado(
        ok=True,
        datos={"total": total, "mostrados": len(recortados), "coincidencias": recortados},
        truncado=total > len(recortados),
    )


# Tramos que devuelve una consulta, y cuanto texto entre todos. La respuesta
# de una herramienta se reenvia en cada vuelta y el agente la recorta a unos
# 2.500 caracteres: mas fragmentos llegarian cortados.
TRAMOS_POR_CONSULTA = 3
MAX_TRAMOS_POR_CONSULTA = 5
MAX_CARACTERES_CONSULTA = 1_800


def consultar_documentos(
    ctx: Contexto, pregunta: str, maximo: int = TRAMOS_POR_CONSULTA
) -> Resultado:
    """
    Los tramos de los documentos que mejor responden a una pregunta.

    Recuperacion lexica (BM25) sobre texto sin tildes ni mayusculas: sirve
    cuando no se sabe el texto exacto que hay que buscar ("cedula del
    trabajador accidentado", "fecha de la incapacidad").
    """
    if not pregunta or not str(pregunta).strip():
        return Resultado(ok=False, error="falta el argumento 'pregunta'")
    if len(str(pregunta)) > 300:
        return Resultado(ok=False, error="la pregunta es demasiado larga: "
                                         "usa las palabras clave del dato")
    try:
        maximo = int(maximo)
    except (TypeError, ValueError):
        maximo = TRAMOS_POR_CONSULTA
    maximo = min(max(1, maximo), MAX_TRAMOS_POR_CONSULTA)
    tramos = ctx.workspace.consultar(
        str(pregunta), maximo=maximo, caracteres=MAX_CARACTERES_CONSULTA)
    if not tramos:
        return Resultado(ok=True, datos={
            "tramos": [],
            "nota": "ninguna palabra de la pregunta aparece en los documentos",
        })
    # Igual que una lectura: es texto de un tercero.
    for t in tramos:
        t["texto"] = sanear_fragmento(t["texto"], t["documento"]).markdown
    return Resultado(ok=True, datos={"tramos": tramos})


# ── Herramientas de catalogo ──

def consultar_catalogo(ctx: Contexto, nombre: str) -> Resultado:
    """
    Devuelve un catalogo de valores validos.

    Sirve para dos cosas: que el agente no invente codigos, y que la
    validacion posterior tenga una fuente unica contra la cual comparar.
    """
    if not nombre:
        return Resultado(
            ok=False,
            error=f"falta 'nombre'. Catalogos: {', '.join(sorted(ctx.catalogos))}",
        )
    catalogo = ctx.catalogos.get(nombre)
    if catalogo is None:
        return Resultado(
            ok=False,
            error=f"no existe el catalogo '{nombre}'. "
                  f"Disponibles: {', '.join(sorted(ctx.catalogos))}",
        )
    return Resultado(
        ok=True,
        datos={"catalogo": nombre,
               "valores": [{"codigo": k, "descripcion": v}
                           for k, v in sorted(catalogo.items(), key=lambda kv: str(kv[0]))]},
    )


# ── Herramientas de validacion, calculo puro ──

def _digito_verificacion_nit(numero: str) -> int:
    """
    Digito de verificacion del NIT colombiano.

    Algoritmo oficial de la DIAN: se ponderan los digitos de derecha a
    izquierda con una serie fija de primos y se toma el modulo 11.
    """
    pesos = (3, 7, 13, 17, 19, 23, 29, 37, 41, 43, 47, 53, 59, 67, 71)
    total = 0
    for i, c in enumerate(reversed(numero)):
        if i >= len(pesos):
            break
        total += int(c) * pesos[i]
    resto = total % 11
    # Con resto 0 o 1 el digito es el propio resto (Ecopetrol: 899999068-1).
    return resto if resto in (0, 1) else 11 - resto


def _codigo_de_tipo(tipo: str) -> Optional[int]:
    """
    Traduce lo que declare el modelo ("CC", "1", "cedula") al codigo de SGDEA.

    Devuelve None si no lo declaro o no se reconoce: no se supone un tipo, se
    informa que falta.
    """
    t = str(tipo or "").strip().upper()
    if not t:
        return None
    if t.isdigit() and int(t) in LONGITUD_POR_TIPO or (
            t.isdigit() and int(t) == int(TipoDocumento.PA)):
        return int(t)
    return CODIGO_TO_TIPO_DOCUMENTO.get(t)


def _declara_nit(tipo: str) -> bool:
    """
    Si el tipo declarado dice NIT, en cualquiera de sus formas.

    Se compara contra `models.enums`, no contra una lista escrita aqui: el
    codigo del NIT lo decide SGDEA.
    """
    t = str(tipo or "").strip().upper()
    if not t:
        return False
    return t in ("NIT", TIPO_DOCUMENTO_CODIGO[int(TipoDocumento.NI)],
                 str(int(TipoDocumento.NI)))


def _a_digitos_ascii(texto: str) -> str:
    """
    El texto con cada digito de cualquier alfabeto traducido a ASCII.

    `str.isdigit()` es cierto para los digitos de ancho completo y para los
    arabigo-indios, no solo para 0-9: sin esto, un documento escrito en esos
    caracteres pasaria por numerico y quedaria en SGDEA con un glifo que
    ninguna cedula real usa. Lo que no es un digito en ningun alfabeto se deja
    igual, para que la comprobacion de mas abajo lo siga viendo como texto.
    """
    convertido = []
    for c in texto:
        try:
            convertido.append(str(unicodedata.digit(c)))
        except (TypeError, ValueError):
            convertido.append(c)
    return "".join(convertido)


def validar_documento(ctx: Contexto, numero: str, tipo: str = "") -> Resultado:
    """
    Valida un numero de documento por estructura, no por consulta externa.
    """
    if not numero:
        return Resultado(ok=False, error="falta el argumento 'numero'")
    sin_separadores = re.sub(r"[.\s\-]", "", str(numero))
    limpio = _a_digitos_ascii(sin_separadores)
    digitos_traducidos = limpio != sin_separadores

    codigo_tipo = _codigo_de_tipo(tipo)
    if not limpio.isdigit():
        # Un pasaporte lleva letras y no se mide en digitos. Rechazarlo aqui
        # contradice al prompt, que manda reportarlo entero y validarlo con esta
        # herramienta: el agente recibiria que su pasaporte no vale y
        # descartaria el campo.
        if codigo_tipo in TIPOS_ALFANUMERICOS or es_pasaporte(limpio):
            return Resultado(ok=True, datos={
                "numero": limpio,
                "longitud": len(limpio),
                "alfanumerico": True,
                # No se mide en digitos: la tabla de longitudes no le aplica.
                "longitud_valida_para_el_tipo": None,
                "tipos_compatibles_con_esta_longitud": [
                    {"codigo": int(TipoDocumento.PA),
                     "descripcion": _NOMBRE_TIPO_DOCUMENTO[int(TipoDocumento.PA)]}
                ],
                "observaciones": [
                    "tiene forma de pasaporte: se reporta entero, con sus "
                    "letras, y el tipo de documento va como pasaporte"
                ],
            })
        return Resultado(
            ok=False,
            error=f"'{numero}' no es un numero de documento: contiene caracteres "
                  f"que no son digitos, y solo el pasaporte los lleva",
        )

    encaja = longitud_valida(limpio, codigo_tipo)
    compatibles = tipos_compatibles(limpio)

    analisis: Dict[str, Any] = {
        "numero": limpio,
        "longitud": len(limpio),
        # None significa "no se pudo comprobar", que no es lo mismo que False.
        # Sin el tipo declarado no hay contra que medir: un numero de seis
        # digitos es una cedula de extranjeria valida y una cedula de
        # ciudadania tambien.
        "longitud_valida_para_el_tipo": encaja,
        "tipos_compatibles_con_esta_longitud": [
            {"codigo": c, "descripcion": _NOMBRE_TIPO_DOCUMENTO.get(c, str(c))}
            for c in compatibles
        ],
        "observaciones": [],
    }

    if digitos_traducidos:
        analisis["observaciones"].append(
            f"'{numero}' esta escrito con digitos de otro alfabeto; se "
            f"reporta como '{limpio}', que es lo que hay que guardar"
        )
    if not compatibles and not es_pasaporte(str(numero)):
        analisis["observaciones"].append(
            f"{len(limpio)} digitos no corresponden a ningun tipo de documento: "
            f"el mas corto es de {MIN_DIGITOS} y el mas largo de {MAX_DIGITOS}"
        )
    if encaja is False:
        minimo, maximo = rango_aceptable(int(codigo_tipo))
        nombre_tipo = _NOMBRE_TIPO_DOCUMENTO.get(int(codigo_tipo), str(codigo_tipo))
        analisis["observaciones"].append(
            f"{len(limpio)} digitos no encajan con {nombre_tipo}, que lleva "
            f"entre {minimo} y {maximo}"
        )
    if encaja is None and codigo_tipo is None and compatibles:
        analisis["observaciones"].append(
            "no se declaro el tipo: la longitud es compatible con "
            + ", ".join(
                _NOMBRE_TIPO_DOCUMENTO.get(c, str(c)) for c in compatibles)
        )
    if limpio.startswith("0"):
        analisis["observaciones"].append(
            "empieza en cero, poco habitual en documentos reales"
        )
    if len(set(limpio)) == 1:
        analisis["observaciones"].append(
            "todos los digitos iguales: casi seguro es un dato de relleno"
        )

    # El digito de verificacion es del NIT; una cedula no lo lleva. Solo se
    # evalua cuando el tipo declarado es NIT.
    if len(limpio) >= 9:
        base = limpio[:9]
        dv = _digito_verificacion_nit(base)
        if _declara_nit(tipo):
            analisis["nit_base"] = base
            analisis["nit_digito_verificacion_calculado"] = dv
            if len(limpio) == 10:
                coincide = int(limpio[9]) == dv
                analisis["digito_verificacion_coincide"] = coincide
                if not coincide:
                    analisis["observaciones"].append(
                        f"el digito de verificacion del NIT no cuadra: se "
                        f"esperaba {dv}"
                    )
        elif not tipo and len(limpio) == 10:
            # Sin saber el tipo no se puede objetar nada: se informa y se pide
            # el dato que falta, en vez de suponer que es un NIT.
            analisis["si_fuera_nit_el_digito_seria"] = dv
            analisis["observaciones"].append(
                "no se declaro el tipo: si fuera NIT el digito de verificacion "
                f"deberia ser {dv}; si es cedula, no lleva digito de "
                "verificacion y la longitud es normal"
            )

    return Resultado(ok=True, datos=analisis)


def consultar_persona(
    ctx: Contexto, numero_documento: str, tipo_documento: Any = None
) -> Resultado:
    """
    Consulta los datos de una persona en el backend.

    Es de solo lectura. Si no hay funcion de consulta configurada lo dice, en
    lugar de devolver vacio: el agente necesita distinguir "no hay servicio"
    de "la persona no existe".

    El registro exige el tipo de documento y no valida la sigla: preguntar con
    la equivocada devuelve lo mismo que preguntar por alguien que no existe. Por
    eso conviene pasarlo cuando se conoce, y por eso "no encontrado" nunca es
    razon para descartar el documento.
    """
    if not numero_documento:
        return Resultado(ok=False, error="falta el argumento 'numero_documento'")
    if ctx.consultar_persona is None:
        return Resultado(
            ok=False,
            error="la consulta de personas no esta disponible en esta corrida",
        )
    try:
        if tipo_documento in (None, ""):
            datos = ctx.consultar_persona(str(numero_documento))
        else:
            datos = ctx.consultar_persona(str(numero_documento), tipo_documento)
    except TypeError:
        # Una consulta que no admite el tipo sigue siendo utilizable.
        datos = ctx.consultar_persona(str(numero_documento))
    except Exception as e:
        return Resultado(ok=False, error=f"la consulta fallo: {type(e).__name__}")
    if not datos:
        return Resultado(
            ok=True,
            datos={
                "encontrado": False,
                "numero_documento": str(numero_documento),
                "nota": "no estar en el registro no invalida el documento: la "
                        "persona puede no estar vinculada, o el tipo consultado "
                        "no ser el suyo",
            },
        )
    return Resultado(ok=True, datos={"encontrado": True, **datos})


# ── Salida estructurada ──

CAMPOS_REPORTABLES = (
    "tipo_remitente", "tipo_documento_afectado", "numero_documento_afectado",
    "nombre_afectado", "tipo_documento_remitente", "numero_documento_remitente",
    "nombre_remitente", "tipo_solicitud", "sexo_afectado",
    "telefono_afectado", "direccion_afectado",
)


def reportar_hallazgo(
    ctx: Contexto,
    campo: str,
    valor: Any,
    evidencia: str,
    confianza: float,
) -> Resultado:
    """
    Registra un dato extraido. La evidencia es obligatoria.

    Solo acumula: no radica. Eso lo decide el pipeline tras la validacion.
    """
    if campo not in CAMPOS_REPORTABLES:
        return Resultado(
            ok=False,
            error=f"campo desconocido '{campo}'. "
                  f"Validos: {', '.join(CAMPOS_REPORTABLES)}",
        )
    if not evidencia or len(str(evidencia).strip()) < 10:
        return Resultado(
            ok=False,
            error="'evidencia' es obligatoria y debe citar el texto o el "
                  "documento de donde sale el dato (minimo 10 caracteres)",
        )
    try:
        conf = float(confianza)
    except (TypeError, ValueError):
        return Resultado(ok=False, error="'confianza' debe ser un numero entre 0 y 1")
    if not 0.0 <= conf <= 1.0:
        return Resultado(ok=False, error="'confianza' debe estar entre 0 y 1")

    ctx.hallazgos.append({
        "campo": campo,
        "valor": valor,
        "evidencia": str(evidencia)[:500],
        "confianza": conf,
    })
    return Resultado(ok=True, datos={"registrado": campo, "total_hallazgos": len(ctx.hallazgos)})


# ── Registro ──

def pedir_revision(ctx: Contexto) -> Resultado:
    """
    Delega en un segundo agente la revision de lo que se va a enviar.

    El revisor tiene sus propias reglas y su propio contexto: relee los
    documentos ya convertidos y responde, campo por campo, si el valor esta
    respaldado por el texto. Existe como herramienta y no como paso fijo para
    que sea el agente quien decida cuando ya tiene algo que revisar.

    Devuelve el veredicto tal cual. Quien decide que hacer con el sigue siendo
    el agente: esta herramienta no cambia ningun valor.
    """
    if ctx.revisar is None:
        return Resultado(
            ok=False,
            error="la revision no esta disponible en esta corrida")

    valores: Dict[str, Any] = dict(ctx.resueltos or {})
    for h in ctx.hallazgos:
        campo = h.get("campo")
        if campo:
            valores[campo] = h.get("valor")
    if not valores:
        return Resultado(
            ok=False,
            error="no hay ningun valor que revisar: reporta algo primero")

    revision = ctx.revisar(valores)
    return Resultado(ok=True, datos={
        "revisado": sorted(valores),
        "veredicto": revision.resumen(),
        "hay_reparos": revision.hay_reparos,
    })


HERRAMIENTAS: Dict[str, Callable[..., Resultado]] = {
    "listar_documentos": listar_documentos,
    "leer_documento": leer_documento,
    "buscar_en_documentos": buscar_en_documentos,
    "consultar_documentos": consultar_documentos,
    "consultar_catalogo": consultar_catalogo,
    "validar_documento": validar_documento,
    "consultar_persona": consultar_persona,
    "reportar_hallazgo": reportar_hallazgo,
    "pedir_revision": pedir_revision,
}


def invocar(ctx: Contexto, nombre: str, argumentos: Optional[Dict[str, Any]] = None) -> Resultado:
    """
    Punto unico de invocacion.

    Centralizarlo permite contabilizar el uso, validar el nombre y garantizar
    que no se pueda llamar nada que no este en el registro, ni siquiera si el
    modelo inventa un nombre de herramienta.
    """
    funcion = HERRAMIENTAS.get(nombre)
    if funcion is None:
        return Resultado(
            ok=False,
            error=f"no existe la herramienta '{nombre}'. "
                  f"Disponibles: {', '.join(sorted(HERRAMIENTAS))}",
        )
    args = dict(argumentos or {})
    try:
        return funcion(ctx, **args)
    except TypeError as e:
        return Resultado(
            ok=False,
            error=f"argumentos incorrectos para '{nombre}': {e}",
        )
    except ErrorHerramienta as e:
        return Resultado(ok=False, error=str(e))
    except Exception as e:
        # Una herramienta que revienta tumba el bucle del agente y con el todo
        # el correo. Se devuelve como error para que el modelo lo lea, cambie
        # de estrategia y siga: el fallo queda en la respuesta, no escondido.
        return Resultado(
            ok=False,
            error=f"la herramienta '{nombre}' fallo: {type(e).__name__}: {e}",
        )
