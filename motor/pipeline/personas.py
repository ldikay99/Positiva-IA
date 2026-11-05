"""
Consulta de personas contra el registro de SGDEA.

Rellena huecos: se usa para los campos que ni el algoritmo ni el modelo
resolvieron, y nunca sustituye un dato leido del correo. El registro dice lo que
Positiva tiene guardado; el correo dice lo que el ciudadano acaba de escribir, y
puede ser justamente la actualizacion que esta pidiendo.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

from models.enums import TipoDocumento

# El endpoint espera la sigla del tipo, y no es la misma que usa
# `models.enums.TIPO_DOCUMENTO_CODIGO`: alli PPT es "PT", NUIP es "NP" y NIT es
# "NI". Mandar la nuestra no da error, da un 404 indistinguible de "no existe".
SIGLA_DEL_ENDPOINT: Dict[int, str] = {
    TipoDocumento.CC: "CC",
    TipoDocumento.CE: "CE",
    TipoDocumento.CD: "CD",
    TipoDocumento.PPT: "PPT",
    TipoDocumento.PE: "PEP",
    TipoDocumento.TI: "TI",
    TipoDocumento.NP: "NUIP",
    TipoDocumento.NI: "NIT",
    TipoDocumento.PA: "PA",
}

# Con que sigla se pregunta cuando el tipo no se conoce. No se recorren todas:
# el endpoint devuelve el mismo 404 para una sigla equivocada que para un
# documento inexistente, asi que probar nueve no distingue nada y multiplica por
# nueve las peticiones contra produccion.
SIGLA_POR_DEFECTO = "CC"

TIEMPO_MAXIMO = 15.0

# Tras cuantos agotamientos SEGUIDOS se deja de preguntar en esta corrida.
#
# El tiempo maximo se paga por correo, y cuando el registro no responde lo paga
# igual: veinte correos son cinco minutos de espera pura dentro de una ventana
# de treinta, sin sacar un solo dato. Que dos consultas seguidas se agoten no
# dice nada de la tercera por si mismo, pero medido en produccion las veinte
# entradas de una jornada estaban todas entre 15,1 y 15,6 segundos —o sea,
# todas contra el tope—: cuando el registro se cae, se cae para el lote entero.
#
# La cuenta se reinicia con cualquier respuesta, asi que un bache no apaga la
# consulta para lo que queda de corrida.
AGOTAMIENTOS_PARA_DEJAR_DE_PREGUNTAR = 3


def _es_por_tiempo(error: BaseException) -> bool:
    """
    Si el fallo fue por agotarse el tiempo de espera.

    Se mira el nombre del tipo y no la clase para no atar este modulo al
    cliente HTTP: `cliente` se inyecta y en las pruebas no es `httpx`.
    """
    nombres = {type(e).__name__.lower()
               for e in (error, error.__cause__, error.__context__) if e}
    return any("timeout" in n or "timedout" in n for n in nombres)


class ConsultaDePersonaFallo(RuntimeError):
    """
    La consulta no se pudo completar.

    Es distinto de que la persona no exista: eso se responde con None. Un fallo
    de red o un 500 no autorizan a concluir nada sobre el registro.
    """


@dataclass
class ConsultaDePersonas:
    """
    Cliente del registro de personas.

    `cliente` se inyecta para poder probar sin red. Debe exponer
    `get(url, params=..., headers=..., timeout=...)` como `httpx`.
    """

    url_base: str
    cliente: Any = None
    # Agotamientos seguidos. Vive en el cliente porque el orquestador lo
    # construye una vez por corrida y lo comparte entre los correos del lote.
    agotamientos_seguidos: int = 0

    def __call__(
        self, numero_documento: str, tipo_documento: Any = None
    ) -> Optional[Dict[str, Any]]:
        """
        Datos del registro, o None si no hay nadie con ese documento.

        `tipo_documento` acepta el codigo interno o la sigla. Sin el se pregunta
        por cedula de ciudadania, que es lo que trae la inmensa mayoria de los
        correos; el 404 resultante no prueba que la persona no exista, solo que
        no esta con ESE tipo.
        """
        numero = "".join(c for c in str(numero_documento or "") if c.isdigit())
        if not numero:
            raise ConsultaDePersonaFallo(
                "no hay numero de documento por el que preguntar")

        respuesta = self._pedir(numero, _sigla(tipo_documento))
        if respuesta.status_code == 404:
            return None
        if respuesta.status_code >= 400:
            raise ConsultaDePersonaFallo(
                f"el registro respondio {respuesta.status_code}: "
                f"{respuesta.text[:200]}")
        try:
            datos = respuesta.json()
        except ValueError as e:
            raise ConsultaDePersonaFallo(
                f"el registro respondio algo que no es JSON: {e}") from e
        if not isinstance(datos, dict) or not datos.get("numeroDocumento"):
            return None
        return _util(datos)

    def consultar(
        self, numero_documento: str, tipo_documento: Any = None
    ) -> Optional[Dict[str, Any]]:
        """
        Igual que llamar al objeto. Es el nombre que espera el orquestador.

        No captura la excepcion: un fallo de consulta tiene que llegar arriba
        para que la bitacora lo registre y la herramienta lo reporte como error
        y no como persona inexistente.
        """
        return self(numero_documento, tipo_documento)

    def _pedir(self, numero: str, sigla: str):
        if self.agotamientos_seguidos >= AGOTAMIENTOS_PARA_DEJAR_DE_PREGUNTAR:
            # No se pregunta y se dice por que. Insistir cuesta el tiempo
            # maximo por correo y no ha devuelto nada en los anteriores.
            raise ConsultaDePersonaFallo(
                f"no se consulta el registro: se agoto el tiempo en "
                f"{self.agotamientos_seguidos} consultas seguidas de esta "
                f"corrida, cada una de {TIEMPO_MAXIMO:.0f}s")
        cliente = self.cliente
        if cliente is None:
            import httpx

            cliente = httpx
        try:
            respuesta = cliente.get(
                f"{self.url_base.rstrip('/')}/personas",
                params={"tipoDocumento": sigla, "numeroDocumento": numero},
                headers={"accept": "application/json"},
                timeout=TIEMPO_MAXIMO,
            )
        except Exception as e:
            if _es_por_tiempo(e):
                self.agotamientos_seguidos += 1
            raise ConsultaDePersonaFallo(
                f"no se pudo consultar el registro: {type(e).__name__}: {e}"
            ) from e
        # Cualquier respuesta, incluido un 404 o un 500, prueba que el servicio
        # contesta: la cuenta vuelve a cero.
        self.agotamientos_seguidos = 0
        return respuesta


# El registro y el payload de radicacion usan catalogos de genero INVERTIDOS
# entre si. No es una suposicion: el payload se leyo de los logs del job de
# produccion, donde 1 acompaña a nombres masculinos, 2 a femeninos y 3 a
# empresas; y el registro devuelve 2 para las personas cuyo correo declara
# masculino. Copiar el numero tal cual invierte el sexo de cada persona.
#
# Solo se traducen los dos valores comprobados. Cualquier otro se ignora: un
# codigo que no se ha visto no se puede interpretar.
SEXO_DEL_GENERO_DEL_REGISTRO: Dict[int, int] = {1: 2, 2: 1}


def _sexo_del_registro(datos: Dict[str, Any]) -> Optional[int]:
    try:
        return SEXO_DEL_GENERO_DEL_REGISTRO.get(int(datos.get("genero")))
    except (TypeError, ValueError):
        return None


# Campos del payload que el registro puede rellenar, y de donde salen. El
# nombre NO esta: rellenarlo convierte un caso que iba a revision manual en una
# radicacion automatica, y si el documento se extrajo mal registra a otra
# persona.
CAMPOS_DEL_REGISTRO = (
    ("telefono_afectado", "telefono"),
    ("direccion_afectado", "direccion"),
    ("sexo_afectado", "_sexo"),
    ("departamento_afectado", "_departamento"),
    ("municipio_afectado", "_municipio"),
)

# Claves con las que el registro numera la geografia. Son las mismas que espera
# el payload, asi que el valor viaja sin traducir; lo unico que hay que hacer es
# sacarlo del objeto.
_ID_GEOGRAFICO = {"_departamento": ("departamento", "idDepartamento"),
                  "_municipio": ("municipio", "idMunicipio")}


def _id_geografico(datos: Dict[str, Any], clave: str) -> Optional[int]:
    bloque, campo = _ID_GEOGRAFICO[clave]
    valor = (datos.get(bloque) or {}).get(campo)
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None

# El registro se consulta por documento, asi que todo lo que devuelve descansa
# en que ese documento sea el del afectado. Si el correo tambien trae su nombre
# y no es el mismo, la extraccion se equivoco en uno de los dos y no se puede
# saber en cual: rellenar seria escribir el telefono de un desconocido.
CONFIANZA_DEL_REGISTRO = 0.90

# Cuanto texto alrededor del documento cuenta como "junto a él". Una ficha o una
# firma dejan el nombre y la cédula a una o dos líneas.
VENTANA_JUNTO_AL_DOCUMENTO = 150


def nombrado_junto_al_documento(texto: str, documento: str, nombre: str) -> bool:
    """
    Si el correo escribe ese nombre al lado de ese documento.

    Es lo que permite resolver un desacuerdo con el registro sin adivinar: si el
    propio correo pone juntos el documento y el nombre que el registro tiene
    para él, el dato bueno es ese y lo que falló fue la extracción del nombre.
    """
    from utils.generic_filter import _palabras_del_nombre, _palabras_equivalentes

    buscadas = _palabras_del_nombre(nombre)
    if len(buscadas) < 2 or not documento:
        return False
    patron = r"[.\s,]?".join(re.escape(c) for c in documento)
    for hallado in re.finditer(rf"(?<!\d){patron}(?!\d)", texto or ""):
        ventana = _palabras_del_nombre(texto[
            max(0, hallado.start() - VENTANA_JUNTO_AL_DOCUMENTO):
            hallado.end() + VENTANA_JUNTO_AL_DOCUMENTO])
        presentes = sum(
            any(_palabras_equivalentes(b, p) for p in ventana) for b in buscadas)
        if presentes >= max(2, (len(buscadas) + 1) // 2 + 1):
            return True
    return False


def lo_nombra_el_correo(principal: str, remitente: str, nombre: str) -> bool:
    """
    Si el nombre aparece en el asunto o el cuerpo, o es el de quien escribe.

    Un adjunto trae a mas gente que el caso: el medico, el acompañante, los otros
    trabajadores de una planilla. Una cedula junto a un nombre dentro de el dice
    de quien es esa cedula, no que esa persona sea el afectado.
    """
    from utils.generic_filter import (
        _palabras_del_nombre, _palabras_equivalentes, es_el_mismo_titular)

    if remitente and es_el_mismo_titular(remitente, nombre):
        return True
    buscadas = _palabras_del_nombre(nombre)
    if len(buscadas) < 2:
        return False
    presentes = _palabras_del_nombre(principal or "")
    cuantas = sum(any(_palabras_equivalentes(b, p) for p in presentes) for b in buscadas)
    return cuantas >= max(2, (len(buscadas) + 1) // 2 + 1)


def palabras_de_mas(nombre: str, referencia: str) -> list:
    """Palabras de `nombre` que no aparecen, ni parecidas, en `referencia`."""
    from utils.generic_filter import _palabras_del_nombre, _palabras_equivalentes

    conocidas = _palabras_del_nombre(referencia)
    return [p for p in _palabras_del_nombre(nombre)
            if not any(_palabras_equivalentes(p, c) for c in conocidas)]


def rellenar_desde_el_registro(
    hallazgos: Dict[str, Any],
    consulta: Any,
    avisos: Optional[list] = None,
    contradicciones: Optional[list] = None,
    texto: str = "",
    principal: str = "",
) -> Dict[str, Any]:
    """
    Campos que el registro aporta y el correo no traia.

    Devuelve solo lo NUEVO, para que el llamador decida como incorporarlo. No
    sustituye nada: lo que el correo dice puede ser justo la actualizacion que
    el ciudadano esta pidiendo.

    Ni una consulta fallida ni una persona ausente detienen nada: el documento
    ya se extrajo con la forma correcta, y que el registro no la tenga solo
    significa que puede no estar vinculada.

    En `contradicciones` se deja el caso en que el registro SI tiene ese
    documento y lo tiene a nombre de otra persona. Ahi no hay hueco que
    rellenar sino un desacuerdo sobre quien es el afectado, y quien llama tiene
    que poder cortar: uno de los dos datos esta mal y no se sabe cual. La
    excepcion es que `texto`, el del correo y sus adjuntos, escriba el nombre del
    registro junto al documento y que `principal`, el asunto y el cuerpo, tambien
    hable de esa persona: ahi se sabe cual, y el nombre se corrige.

    Se consulta aunque no falte ningun campo, porque el nombre tambien se
    comprueba: un texto cualquiera tomado por nombre no deja hueco que rellenar.
    """
    from pipeline.extraccion import Hallazgo
    from utils.generic_filter import es_el_mismo_titular

    faltan = [(campo, clave) for campo, clave in CAMPOS_DEL_REGISTRO
              if not _tiene_valor(hallazgos.get(campo))]
    if consulta is None:
        return {}

    documento = _valor(hallazgos.get("numero_documento_afectado"))
    if not documento:
        return {}

    try:
        datos = consulta(documento, _valor(hallazgos.get("tipo_documento_afectado")))
    except ConsultaDePersonaFallo as e:
        if avisos is not None:
            avisos.append(f"el registro de personas no respondio: {e}")
        return {}
    if not datos:
        return {}

    nombre_del_correo = _valor(hallazgos.get("nombre_afectado"))
    nombre_del_registro = datos.get("nombre", "")
    nuevos: Dict[str, Any] = {}
    distintos = (nombre_del_correo and nombre_del_registro
                 and not es_el_mismo_titular(nombre_del_correo, nombre_del_registro))
    if ((distintos or not nombre_del_correo) and nombre_del_registro
            and nombrado_junto_al_documento(texto, documento, nombre_del_registro)
            and lo_nombra_el_correo(principal, _valor(hallazgos.get("nombre_remitente")),
                                    nombre_del_registro)):
        distintos = False
        if avisos is not None:
            avisos.append(
                f"nombre del afectado corregido con el registro: el correo "
                f"escribe '{nombre_del_registro}' junto al documento {documento}"
                + (f", y se habia tomado '{nombre_del_correo}'"
                   if nombre_del_correo else ""))
        nuevos["nombre_afectado"] = Hallazgo(
            campo="nombre_afectado", valor=nombre_del_registro,
            evidencia=f"el registro de SGDEA lo tiene asociado al documento "
                      f"{documento} y el correo lo escribe junto a el",
            confianza=CONFIANZA_DEL_REGISTRO, metodo="registro_de_sgdea",
        )
    elif (nombre_del_correo and nombre_del_registro and not distintos
          and palabras_de_mas(nombre_del_correo, nombre_del_registro)):
        # Es el mismo titular y al texto extraido se le pego algo que no es
        # nombre: "Controversia Jose Ruiz Mora", "Jose Ruiz Mora sufrio".
        if avisos is not None:
            avisos.append(
                f"nombre del afectado limpiado con el registro: "
                f"se habia tomado '{nombre_del_correo}'")
        nuevos["nombre_afectado"] = Hallazgo(
            campo="nombre_afectado", valor=nombre_del_registro,
            evidencia=f"el registro de SGDEA lo tiene asociado al documento "
                      f"{documento}, y el correo escribe el mismo nombre",
            confianza=CONFIANZA_DEL_REGISTRO, metodo="registro_de_sgdea",
        )
    if distintos:
        if avisos is not None:
            avisos.append(
                f"el registro tiene el documento {documento} a nombre de otra "
                f"persona que la del correo: no se rellena nada desde ahi"
            )
        if contradicciones is not None:
            contradicciones.append(
                f"el registro de SGDEA tiene el documento {documento} a nombre "
                f"de otra persona, y el correo lo atribuye a "
                f"'{nombre_del_correo}': uno de los dos datos esta mal y no se "
                f"puede saber cual"
            )
        return {}

    for campo, clave in faltan:
        if clave == "_sexo":
            valor = _sexo_del_registro(datos)
        elif clave in _ID_GEOGRAFICO:
            valor = _id_geografico(datos, clave)
        else:
            valor = datos.get(clave)
        if valor in (None, ""):
            continue
        traducido = (f" (el registro lo codifica como genero={datos.get('genero')},"
                     f" que en su catalogo es el contrario al del payload)"
                     if clave == "_sexo" else "")
        nuevos[campo] = Hallazgo(
            campo=campo, valor=valor,
            evidencia=f"el registro de SGDEA lo tiene asociado al documento "
                      f"{documento}; el correo no lo trae{traducido}",
            confianza=CONFIANZA_DEL_REGISTRO, metodo="registro_de_sgdea",
        )
    return nuevos


def _valor(hallazgo: Any) -> str:
    return "" if hallazgo is None else str(getattr(hallazgo, "valor", "") or "")


def _tiene_valor(hallazgo: Any) -> bool:
    return bool(_valor(hallazgo).strip())


def _sigla(tipo_documento: Any) -> str:
    """La sigla que entiende el endpoint, a partir del codigo o de otra sigla."""
    if tipo_documento in (None, ""):
        return SIGLA_POR_DEFECTO
    try:
        return SIGLA_DEL_ENDPOINT[int(tipo_documento)]
    except (TypeError, ValueError):
        pass
    except KeyError:
        return SIGLA_POR_DEFECTO
    texto = str(tipo_documento).strip().upper()
    if texto in SIGLA_DEL_ENDPOINT.values():
        return texto
    # Nuestras siglas internas, que el endpoint no reconoce.
    equivalencias = {"PT": "PPT", "NP": "NUIP", "NI": "NIT", "PE": "PEP"}
    return equivalencias.get(texto, SIGLA_POR_DEFECTO)


def _nombre_completo(datos: Dict[str, Any]) -> str:
    partes = [datos.get(c) for c in ("nombre1", "nombre2", "apellido1", "apellido2")]
    return " ".join(str(p).strip() for p in partes if p and str(p).strip())


def _geo(bloque: Any) -> Dict[str, Any]:
    if not isinstance(bloque, dict):
        return {}
    return {k: v for k, v in bloque.items() if v not in (None, "")}


def _util(datos: Dict[str, Any]) -> Dict[str, Any]:
    """
    Lo que el registro aporta, sin los campos vacios.

    `genero` viaja EN CRUDO, con el numero que devolvio el registro. Traducirlo
    aqui obligaria a este modulo a saber a que catalogo va destinado; la
    conversion vive en el relleno, junto a la evidencia que la justifica.
    """
    salida: Dict[str, Any] = {}
    if datos.get("genero") not in (None, ""):
        salida["genero"] = datos["genero"]
    nombre = _nombre_completo(datos)
    if nombre:
        salida["nombre"] = nombre
    for origen, destino in (("telefono", "telefono"), ("direccion", "direccion"),
                            ("email", "correo"),
                            ("numeroDocumento", "numero_documento"),
                            ("tipoDocumento", "tipo_documento")):
        valor = datos.get(origen)
        if valor not in (None, ""):
            salida[destino] = valor
    for origen, destino in (("pais", "pais"), ("departamento", "departamento"),
                            ("municipio", "municipio")):
        bloque = _geo(datos.get(origen))
        if bloque:
            salida[destino] = bloque
    siniestros = datos.get("siniestros")
    if siniestros:
        salida["siniestros"] = siniestros
    return salida
