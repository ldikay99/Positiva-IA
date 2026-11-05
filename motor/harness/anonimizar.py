"""
Pseudonimizacion consistente para el corpus.

El mismo valor original produce siempre el mismo reemplazo bajo la misma sal,
de modo que los campos que coincidian entre si siguen coincidiendo y el caso
conserva lo que ejercita.

No es anonimizacion criptografica. El corpus se queda en su carpeta y no se
versiona.

Alcance:

  - Solo se sustituyen los nombres que llegan por campo. Los que aparecen
    unicamente en el texto OCR de un adjunto siguen siendo los reales;
    `nombres_probablemente_reales` los saca a la luz.
  - El display name del remitente se sustituye siempre por un nombre de
    persona, asi que el corpus no reproduce los casos en que ese campo trae
    un cargo ("Recursos Humanos", "Coordinadora SST").
  - Las direcciones genericas (contacto@, rrhh@) conservan su parte local,
    porque el pipeline decide con ella.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from servidor_mcp.herramientas import _digito_verificacion_nit

# ── Vocabularios para generar reemplazos verosimiles ──
# El reemplazo conserva la forma del original para que los extractores sigan
# teniendo algo que reconocer.
_NOMBRES = (
    "Alonso", "Beatriz", "Camilo", "Dolores", "Emilio", "Fabiola", "Gustavo",
    "Herminia", "Ignacio", "Julieta", "Leonardo", "Marcela", "Nicolas",
    "Otilia", "Patricio", "Rosalba", "Santiago", "Teresa", "Ulises", "Valeria",
)
_APELLIDOS = (
    "Aguirre", "Bermudez", "Carvajal", "Duarte", "Escalante", "Fuentes",
    "Galindo", "Hurtado", "Idarraga", "Jaramillo", "Lozada", "Montoya",
    "Nieto", "Ocampo", "Pizarro", "Quintero", "Robledo", "Salgado",
    "Toledo", "Urrego", "Valencia", "Zambrano",
)
_EMPRESAS = (
    "Alfa Industrial", "Boreal Agropecuaria", "Cumbre Servicios",
    "Delta Logistica", "Encanto Comercial", "Farallon Construcciones",
    "Guaduas Alimentos", "Horizonte Textil", "Itaca Transportes",
)
_SUFIJOS_EMPRESA = ("S.A.S", "SAS", "LTDA", "S.A.", "E.U.")
_DOMINIOS = (
    "empresa-uno.com", "empresa-dos.com.co", "corp-tres.co",
    "servicios-cuatro.com", "grupo-cinco.com.co",
)

# ── Deteccion de PII ──
# Cada tipo se declara UNA vez; de ahi salen el patron individual y el combinado.
_PAT_CORREO = r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
# Documentos y numeros de referencia: con separadores de miles, o una racha de
# 6 a 20 digitos, que cubre planillas y radicados sin rotulo.
_PAT_DOCUMENTO = r"\b\d{1,3}(?:[.\s]\d{3}){1,3}\b|\b\d{6,20}\b"
# Telefonos. El fijo exige separador, parentesis o indicativo +57: sin esa
# señal la racha de digitos se trata como documento, que es lo mas frecuente
# en este dominio.
_PAT_TELEFONO = (
    # Con indicativo de pais. Va fuera del `\b` porque entre un espacio y un
    # `+` no hay frontera de palabra.
    r"\+\d{1,3}(?:[\s\-.]?\d){6,14}"
    r"|\b(?:"
    r"\+?57[\s\-]\d{3}[\s\-]?\d{3}[\s\-]?\d{4}"      # con indicativo de pais
    r"|3\d{2}[\s\-]?\d{3}[\s\-]?\d{4}"                # celular: 10 digitos, inicia en 3
    r"|\(\d{1,3}\)[\s\-]?\d{3}[\s\-]?\d{4}"           # fijo con indicativo entre parentesis
    r"|[1-8]\d{0,2}[\s\-]\d{3}[\s\-]?\d{4}"           # fijo con separador explicito
    r")\b"
)
# Radicados ENT: se preservan como estructura pero se renumeran
_PAT_RADICADO = r"\bENT\d{10,}\b"

_RE_EMAIL = re.compile(_PAT_CORREO)
_RE_DOCUMENTO = re.compile(_PAT_DOCUMENTO)
_RE_TELEFONO = re.compile(_PAT_TELEFONO)
_RE_RADICADO = re.compile(_PAT_RADICADO, re.IGNORECASE)

# Patron combinado para anonimizar en UNA pasada. El orden de las alternativas
# fija la precedencia: radicado, correo, telefono y por ultimo documento, que es
# el mas permisivo. Los nombres de grupo permiten despachar al generador
# correcto sin volver a evaluar patrones sobre texto ya sustituido.
_RE_TODO_PII = re.compile(
    f"(?P<radicado>{_PAT_RADICADO})"
    f"|(?P<correo>{_PAT_CORREO})"
    f"|(?P<telefono>{_PAT_TELEFONO})"
    f"|(?P<documento>{_PAT_DOCUMENTO})",
    re.IGNORECASE,
)


def _normaliza(texto: str) -> str:
    n = unicodedata.normalize("NFD", (texto or "").lower())
    return "".join(c for c in n if unicodedata.category(c) != "Mn").strip()


def _celular_valido(semilla: int) -> str:
    """
    Celular colombiano falso que EXISTE en el plan de numeracion.

    Se prueban candidatos derivados de la semilla y se acepta el primero que
    la tabla de numeracion da por valido. Sin la libreria, se genera un 3
    seguido de nueve digitos.
    """
    try:
        import phonenumbers
    except ImportError:
        return "3" + str(semilla).zfill(9)[-9:]

    resto = str(semilla).zfill(7)[-7:]
    for intento in range(100):
        # 300..324 y 350..351 caben en este barrido; la libreria decide.
        prefijo = 300 + ((semilla // (intento + 1)) + intento) % 52
        candidato = f"{prefijo}{resto}"
        try:
            if phonenumbers.is_valid_number(phonenumbers.parse(candidato, "CO")):
                return candidato
        except phonenumbers.NumberParseException:
            continue
    return "3" + str(semilla).zfill(9)[-9:]


def _semilla_de(valor: str, sal: str) -> int:
    """Entero estable derivado del valor. Misma entrada, misma salida."""
    h = hashlib.sha256(f"{sal}\x00{_normaliza(valor)}".encode("utf-8")).digest()
    return int.from_bytes(h[:8], "big")


def tiene_forma_de_movil(numero: str) -> bool:
    """Si la racha de digitos es indistinguible de un movil colombiano."""
    return bool(re.fullmatch(r"3\d{9}", re.sub(r"\D", "", numero or "")))


def _documento_falso(semilla: int, longitud: int) -> str:
    """
    Documento falso de la longitud pedida, derivado de la semilla.

    El primer digito nunca es 0, para que siga pareciendo un documento real, y
    en los de diez digitos tampoco es 3, que es la forma de un movil. En los de
    diez se recalcula el ultimo digito con el algoritmo de la DIAN, para que un
    NIT falso siga cuadrando con su digito de verificacion.
    """
    posibles = [d for d in range(1, 10) if not (longitud == 10 and d == 3)]
    primero = posibles[semilla % len(posibles)]
    resto = str(semilla // 9).zfill(longitud)[-(longitud - 1):] if longitud > 1 else ""
    falso = f"{primero}{resto}"[:longitud]
    if longitud == 10:
        falso = falso[:9] + str(_digito_verificacion_nit(falso[:9]))
    return falso


@dataclass
class Anonimizador:
    """
    Reemplaza PII de forma consistente dentro de una misma instancia.

    La `sal` fija el mapeo: la misma sal produce siempre los mismos reemplazos,
    lo que permite regenerar un caso del corpus y obtener identicos valores.
    """

    sal: str = "corpus-pqrd"
    # Registro de lo sustituido, para poder auditar el caso sin ver el original
    mapa: Dict[str, str] = field(default_factory=dict)

    # ── Generadores ──

    def documento(self, original: str) -> str:
        """
        Documento falso que conserva la longitud del original.

        Si tiene forma de NIT (nueve digitos mas el de verificacion) se
        recalcula ese ultimo digito con el algoritmo de la DIAN.

        Un documento de diez digitos nunca empieza en 3: esa es exactamente la
        forma de un movil colombiano.
        """
        solo_digitos = re.sub(r"\D", "", original or "")
        if not solo_digitos:
            return original
        # Si la racha ya se sustituyo por otra via (el patron de telefono se
        # solapa en 8 y 10 digitos), se reutiliza el mismo reemplazo.
        previo = self.mapa.get(_normaliza(solo_digitos))
        if previo:
            return previo
        n = len(solo_digitos)
        s = _semilla_de(solo_digitos, self.sal + "doc")
        falso = _documento_falso(s, n)
        return self._registrar(solo_digitos, falso)

    def nombre_persona(self, original: str) -> str:
        """Nombre falso con la misma cantidad de tokens que el original."""
        if not original or not original.strip():
            return original
        tokens = original.split()
        s = _semilla_de(original, self.sal + "nom")
        partes: List[str] = []
        for i in range(len(tokens)):
            if i < max(1, len(tokens) // 2):
                partes.append(_NOMBRES[(s >> (i * 5)) % len(_NOMBRES)])
            else:
                partes.append(_APELLIDOS[(s >> (i * 5)) % len(_APELLIDOS)])
        falso = " ".join(partes)
        # Preservar la capitalizacion del original: MAYUSCULAS o Titulo
        if original.isupper():
            falso = falso.upper()
        elif original.islower():
            falso = falso.lower()
        return self._registrar(original, falso)

    def razon_social(self, original: str) -> str:
        if not original or not original.strip():
            return original
        s = _semilla_de(original, self.sal + "emp")
        base = _EMPRESAS[s % len(_EMPRESAS)]
        sufijo = _SUFIJOS_EMPRESA[(s >> 8) % len(_SUFIJOS_EMPRESA)]
        falso = f"{base} {sufijo}"
        if original.isupper():
            falso = falso.upper()
        return self._registrar(original, falso)

    def correo(self, original: str) -> str:
        """Correo falso que conserva la forma de la parte local."""
        if not original or "@" not in original:
            return original
        local, _, dominio = original.partition("@")
        s = _semilla_de(original, self.sal + "mail")
        # Conservar si era generico (contacto@, rrhh@) o personal (juan.perez@)
        if "." in local:
            nuevo_local = (
                f"{_NOMBRES[s % len(_NOMBRES)]}."
                f"{_APELLIDOS[(s >> 8) % len(_APELLIDOS)]}"
            ).lower()
        else:
            nuevo_local = local.lower()  # generico: el handle importa, no la persona
        nuevo_dominio = _DOMINIOS[(s >> 16) % len(_DOMINIOS)]
        # Preservar dominios institucionales: el pipeline decide con ellos
        if dominio.lower().endswith(("positiva.gov.co", "positivaseguros.com.co")):
            nuevo_dominio = dominio.lower()
        elif dominio.lower().endswith(".gov.co"):
            nuevo_dominio = "entidad-publica.gov.co"
        falso = f"{nuevo_local}@{nuevo_dominio}"
        return self._registrar(original, falso)

    def telefono(self, original: str) -> str:
        """
        Telefono falso que sigue siendo valido en el plan de numeracion.

        Un celular se genera con un prefijo que existe de verdad; cualquier
        otro numero conserva su longitud.

        El indicativo de pais se conserva.
        """
        crudo = (original or "").strip()
        solo_digitos = re.sub(r"\D", "", crudo)
        if not solo_digitos:
            return original
        previo = self.mapa.get(_normaliza(solo_digitos))
        if previo:
            return previo

        m = re.match(r"\+(\d{1,3})", crudo)
        indicativo = m.group(1) if m else ""
        nacional = solo_digitos[len(indicativo):]
        s = _semilla_de(solo_digitos, self.sal + "tel")
        if nacional.startswith("3") and len(nacional) == 10:
            nuevo = _celular_valido(s)
        else:
            nuevo = str(s).zfill(len(nacional))[-len(nacional):] if nacional else ""
        falso = f"+{indicativo}{nuevo}" if indicativo else nuevo
        return self._registrar(solo_digitos, falso)

    def radicado(self, original: str) -> str:
        s = _semilla_de(original, self.sal + "ent")
        return self._registrar(original, "ENT" + str(s).zfill(13)[-13:])

    # ── Aplicacion sobre texto libre ──

    def texto(self, contenido: str) -> str:
        """
        Anonimiza correos, documentos, telefonos y radicados. Los nombres no se
        tocan aqui: no hay patron fiable para detectarlos, se sustituyen por
        campo con `sustituir_nombres`.

        Una sola pasada con el patron combinado, para no sustituir dos veces el
        mismo dato.
        """
        if not contenido:
            return contenido

        def _sustituir(m: "re.Match[str]") -> str:
            if m.lastgroup == "radicado":
                return self.radicado(m.group(0))
            if m.lastgroup == "correo":
                return self.correo(m.group(0))
            if m.lastgroup == "telefono":
                return self.telefono(m.group(0))
            return self.documento(m.group(0))

        return _RE_TODO_PII.sub(_sustituir, contenido)

    def sustituir_nombres(self, contenido: str, nombres: List[str]) -> str:
        """
        Reemplaza en el texto los nombres que ya se conocen por campo.
        Se aplica despues de `texto()`, con la lista de nombres extraidos.
        """
        t = contenido or ""
        # De mas largo a mas corto, para no romper nombres compuestos
        for original in sorted({n for n in nombres if n and n.strip()},
                              key=len, reverse=True):
            falso = self.mapa.get(_normaliza(original)) or self.nombre_persona(original)
            t = re.sub(re.escape(original), falso, t, flags=re.IGNORECASE)
        return t

    # ── Auditoria ──

    def _registrar(self, original: str, falso: str) -> str:
        self.mapa[_normaliza(original)] = falso
        return falso

    def resumen(self) -> List[Tuple[str, str]]:
        """Cantidad de sustituciones por tipo, sin exponer los originales."""
        return [("sustituciones", str(len(self.mapa)))]


def fugas_de_original(anon: "Anonimizador", *textos: str) -> List[str]:
    """
    Compuerta correcta de anonimizacion: comprueba que ningun valor ORIGINAL
    sobreviva en los textos ya anonimizados.

    Se compara contra las claves del mapa, que son los originales
    normalizados. Si uno aparece en la salida, es una fuga real.
    """
    fugas: List[str] = []
    unido = _normaliza(" \n ".join(t or "" for t in textos))
    if not unido:
        return fugas
    for original, falso in anon.mapa.items():
        # Los originales muy cortos generan coincidencias por azar y no
        # identifican a nadie por si solos.
        if len(original) < 6:
            continue
        # Los buzones genericos y los dominios institucionales se preservan:
        # son roles, no personas, y el pipeline decide con ellos.
        if _normaliza(falso) == original:
            continue
        if original in unido:
            fugas.append(f"{original[:4]}... sobrevivio sin anonimizar")
    return fugas


def nombres_probablemente_reales(contenido: str) -> List[str]:
    """
    Nombres que quedaron sin sustituir, en las posiciones donde va un nombre.

    Cubre el hueco de `fugas_de_original`, que solo compara contra las claves
    del mapa: un nombre que aparece unicamente dentro del texto de un adjunto
    nunca entra al mapa.

    Se busca por CONTEXTO —junto a un documento, detras de un tratamiento o de
    una despedida— y no por forma.

    Un nombre suelto en prosa se escapa. La compuerta de escritura al corpus es
    `fugas_de_original`; esto es la ayuda para revisar a mano.
    """
    if not contenido:
        return []
    from pipeline.extraccion import (
        _CIERRE, _CONECTOR_DOCUMENTO, _ETIQUETAS_AFECTADO, _MARCA_DOCUMENTO,
        _NOMBRE, _candidato_afectado)

    etiquetas = "|".join(_ETIQUETAS_AFECTADO)
    contextos = (
        # "JUAN PEREZ identificado con CC 41234567"
        rf"({_NOMBRE})\s*[,\-]?\s*(?i:{_CONECTOR_DOCUMENTO}{_MARCA_DOCUMENTO})"
        rf"\s*(?i:n[o°º]\.?|nro\.?)?\s*[:\-.]?\s*\d",
        # "el trabajador JUAN PEREZ", "señora Maria Gomez"
        rf"(?i:\b(?:{etiquetas})\b)\s*(?:\(a\))?\s*[:\-]?\s*({_NOMBRE})",
        # "Cordialmente, JUAN PEREZ"
        rf"(?i:\b(?:{'|'.join(_CIERRE)})\b)\s*[,.]?\s*\n?\s*({_NOMBRE})",
        # "Historia clinica de JUAN PEREZ", "a nombre de JUAN PEREZ". El
        # sustantivo documental va delante y no puede ser parte del nombre.
        rf"(?i:\b(?:nombre|historia\s+cl[ií]nica|epicrisis|incapacidad"
        rf"|certificado|constancia|furat|dictamen|reporte)\b)"
        rf"\s+(?i:de|del)\s+({_NOMBRE})",
    )

    vocabulario = {_normaliza(p) for p in _NOMBRES + _APELLIDOS}
    sospechosos: List[str] = []
    for patron in contextos:
        for m in re.finditer(patron, contenido):
            h = _candidato_afectado(contenido, m.group(1), m.start(), "", 0.8)
            if h is None:
                continue
            partes = [_normaliza(p) for p in str(h.valor).split()]
            if any(p in vocabulario for p in partes):
                continue
            sospechosos.append(str(h.valor))
    return sospechosos


def verificar_sin_pii(contenido: str) -> List[str]:
    """
    Revisa que un texto ya anonimizado no conserve PII detectable.

    Se usa como red de seguridad antes de escribir un caso al corpus: si algo
    se escapo del anonimizador, esto lo delata en lugar de guardarlo.
    Devuelve la lista de hallazgos; vacia significa limpio.
    """
    hallazgos: List[str] = []
    if not contenido:
        return hallazgos
    for etiqueta, patron in (
        ("correo", _RE_EMAIL),
        ("documento", _RE_DOCUMENTO),
        ("telefono", _RE_TELEFONO),
    ):
        for m in patron.finditer(contenido):
            hallazgos.append(f"{etiqueta}: {m.group(0)[:6]}...")
    return hallazgos


if __name__ == "__main__":
    raise SystemExit(
        "harness.anonimizar no es una herramienta: es el sustituidor de PII "
        "que usan el recolector y el reparador del corpus.\n"
        "  Recolectar casos del buzon real:  python -m harness.recolectar\n"
        "  Revisar valores mal formados:     "
        "python -m harness.reparar_corpus --revisar"
    )
