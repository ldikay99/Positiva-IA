"""
Modelo de un caso del corpus.

Un caso es un correo real anonimizado mas la salida que el pipeline DEBERIA
producir. Es la unidad de medida del proyecto: sin casos etiquetados no se puede
afirmar que un cambio mejoro nada.

Formato en disco: un directorio por caso, con el correo y sus adjuntos en
Markdown y un `esperado.json` con los campos etiquetados. Directorio y no un
solo archivo porque los adjuntos pueden ser varios y grandes, y porque asi el
caso se puede inspeccionar a mano.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

# Campos que el pipeline produce y que el corpus etiqueta. El nombre coincide
# con el del payload de radicacion para que la comparacion sea directa.
CAMPOS_EVALUABLES = (
    "tipo_remitente",
    "tipo_documento_afectado",
    "numero_documento_afectado",
    "nombre_afectado",
    "tipo_documento_remitente",
    "numero_documento_remitente",
    "nombre_remitente",
    "tipo_pqrd",
    "tipo_solicitud",
    "oficina",
    "sexo_afectado",
    "telefono_afectado",
    "direccion_afectado",
    "accion",  # radicado | duplicado | reenviado | manual
)


@dataclass
class Esperado:
    """
    Salida correcta de un caso.

    Un campo en None significa "no evaluado": el caso no afirma nada sobre el.
    Es distinto de la cadena vacia, que significa "debe quedar vacio". La
    diferencia importa: sin ella no se puede etiquetar un caso parcialmente.
    """

    tipo_remitente: Optional[int] = None
    tipo_documento_afectado: Optional[int] = None
    numero_documento_afectado: Optional[str] = None
    nombre_afectado: Optional[str] = None
    tipo_documento_remitente: Optional[int] = None
    numero_documento_remitente: Optional[str] = None
    nombre_remitente: Optional[str] = None
    tipo_pqrd: Optional[int] = None
    tipo_solicitud: Optional[int] = None
    oficina: Optional[int] = None
    sexo_afectado: Optional[int] = None
    telefono_afectado: Optional[str] = None
    direccion_afectado: Optional[str] = None
    accion: Optional[str] = None

    def campos_etiquetados(self) -> List[str]:
        d = asdict(self)
        return [k for k, v in d.items() if v is not None]


@dataclass
class Caso:
    """Un correo real anonimizado, con su salida esperada."""

    id: str
    asunto: str
    remitente_nombre: str
    remitente_correo: str
    sender_nombre: str = ""
    cuerpo_md: str = ""
    hilo_md: str = ""
    adjuntos: Dict[str, str] = field(default_factory=dict)  # nombre -> markdown
    esperado: Esperado = field(default_factory=Esperado)
    # Que aporta este caso al corpus. Obligatorio.
    motivo: str = ""
    # Etiquetas para filtrar subconjuntos: "buzon-corporativo", "inyeccion",
    # "empresa", "reenvio-interno"...
    etiquetas: List[str] = field(default_factory=list)

    # ── Persistencia ──

    def guardar(self, raiz: Path) -> Path:
        destino = Path(raiz) / self.id
        destino.mkdir(parents=True, exist_ok=True)
        (destino / "correo.md").write_text(self.cuerpo_md, encoding="utf-8")
        if self.hilo_md:
            (destino / "hilo.md").write_text(self.hilo_md, encoding="utf-8")
        if self.adjuntos:
            carpeta = destino / "adjuntos"
            carpeta.mkdir(exist_ok=True)
            for nombre, contenido in self.adjuntos.items():
                (carpeta / f"{_slug(nombre)}.md").write_text(contenido, encoding="utf-8")
        meta = {
            "id": self.id,
            "asunto": self.asunto,
            "remitente_nombre": self.remitente_nombre,
            "remitente_correo": self.remitente_correo,
            "sender_nombre": self.sender_nombre,
            "motivo": self.motivo,
            "etiquetas": self.etiquetas,
            "adjuntos": list(self.adjuntos.keys()),
        }
        (destino / "caso.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (destino / "esperado.json").write_text(
            json.dumps(asdict(self.esperado), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return destino

    @classmethod
    def cargar(cls, directorio: Path) -> "Caso":
        d = Path(directorio)
        meta = json.loads((d / "caso.json").read_text(encoding="utf-8"))
        esperado_bruto = json.loads((d / "esperado.json").read_text(encoding="utf-8"))
        adjuntos: Dict[str, str] = {}
        carpeta = d / "adjuntos"
        if carpeta.is_dir():
            for archivo in sorted(carpeta.glob("*.md")):
                adjuntos[archivo.stem] = archivo.read_text(encoding="utf-8")
        hilo = d / "hilo.md"
        return cls(
            id=meta["id"],
            asunto=meta.get("asunto", ""),
            remitente_nombre=meta.get("remitente_nombre", ""),
            remitente_correo=meta.get("remitente_correo", ""),
            sender_nombre=meta.get("sender_nombre", ""),
            cuerpo_md=(d / "correo.md").read_text(encoding="utf-8"),
            hilo_md=hilo.read_text(encoding="utf-8") if hilo.exists() else "",
            adjuntos=adjuntos,
            esperado=Esperado(**esperado_bruto),
            motivo=meta.get("motivo", ""),
            etiquetas=meta.get("etiquetas", []),
        )


def _slug(nombre: str) -> str:
    limpio = "".join(c if (c.isalnum() or c in "-_") else "-" for c in nombre)
    while "--" in limpio:
        limpio = limpio.replace("--", "-")
    return limpio.strip("-").lower() or "adjunto"


# Marca que deja el conversor en una pagina de la que no se pudo sacar texto.
_PAGINA_SIN_TEXTO = "(pagina sin texto extraible)"
_ENCABEZADO = re.compile(r"(?m)^#{1,6}[^\n]*$")


def adjuntos_ilegibles_de(caso: Caso) -> List[str]:
    """
    Adjuntos del caso de los que no se pudo leer nada.

    En produccion los marca el conversor y llegan a `extraer_todo`, que
    distingue "no hay documento" de "el documento esta en un escaneo que no se
    pudo leer". El corpus guarda el markdown ya convertido, asi que la
    condicion se reconstruye de su contenido.

    Basta que UNA pagina haya dado texto para que el adjunto no cuente como
    ilegible, igual que en la conversion.
    """
    ilegibles: List[str] = []
    for nombre, markdown in (caso.adjuntos or {}).items():
        cuerpo = _ENCABEZADO.sub(" ", markdown or "")
        cuerpo = cuerpo.replace(_PAGINA_SIN_TEXTO, " ").replace("_", " ")
        if not cuerpo.strip():
            ilegibles.append(nombre)
    return ilegibles


def extraer_de(caso: Caso):
    """
    Corre la extraccion determinista sobre un caso.

    Punto unico de entrada para cobertura, exactitud y regresion.
    """
    from pipeline.extraccion import extraer_todo

    return extraer_todo(
        asunto=caso.asunto,
        cuerpo=caso.cuerpo_md,
        texto_adjuntos="\n".join(caso.adjuntos.values()),
        adjuntos_ilegibles=adjuntos_ilegibles_de(caso),
        from_name=caso.remitente_nombre,
        from_address=caso.remitente_correo,
        sender_name=caso.sender_nombre,
        nombres_hilo=[(caso.remitente_nombre, caso.remitente_correo)],
        cuerpo_actual=caso.cuerpo_md,
    )


def _no_es_una_herramienta() -> str:
    return (
        "harness.caso no es una herramienta: es el modelo de un caso del corpus "
        "y el punto de entrada que comparten las medidas.\n"
        "  Ver el corpus y etiquetarlo:  python -m harness.etiquetar --estado\n"
        "  Acierto del extractor:        python -m harness.exactitud\n"
        "  Lo que llega a SGDEA:         python -m harness.enviado"
    )


def cargar_corpus(raiz: Path, etiqueta: Optional[str] = None) -> List[Caso]:
    """Carga todos los casos del corpus, opcionalmente filtrando por etiqueta."""
    raiz = Path(raiz)
    if not raiz.is_dir():
        return []
    casos: List[Caso] = []
    for directorio in sorted(raiz.iterdir()):
        if not (directorio / "caso.json").exists():
            continue
        caso = Caso.cargar(directorio)
        if etiqueta and etiqueta not in caso.etiquetas:
            continue
        casos.append(caso)
    return casos


if __name__ == "__main__":
    raise SystemExit(_no_es_una_herramienta())
