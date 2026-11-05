"""
Cuanto trae un correo, y desde cuando deja de valer la pena procesarlo.

Un correo con decenas de paginas escaneadas no se atiende mejor por pasarlo
entero: satura el reconocimiento de texto y le da al agente mas informacion de
la que puede sopesar para decidir un dato. Se corta antes de convertir y antes
de llamar al modelo, y se cierra a revision manual con su motivo.

Los topes salen de medir el buzon, no de estimar. Sobre 60 correos reales:

    adjuntos              media 2,0   mediana 1   p95  5   max 21
    paginas               media 4,7   mediana 2   p95 19   max 48
    imagenes a reconocer  media 2,6   mediana 1   p95 11   max 27
    tamaño                media 1,7   mediana 0,3 p95 9,8  max 15,1 MB

Estan puestos por encima del percentil 95 a proposito: el correo corriente —un
adjunto y dos paginas— no puede quedar fuera por esto. Se ajustan por entorno
para poder apretarlos mirando datos, sin desplegar.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

# Motivo con el que se cierra. No esta entre los que se aplazan: el correo no
# va a traer menos paginas en la corrida siguiente.
MOTIVO = "VOLUMEN_EXCESIVO"

# Solo se cuentan las paginas del documento hasta este punto: pasado el tope ya
# se decidio, y abrir el resto no cambia la respuesta.
LIMITE_PAGINAS_QUE_SE_CUENTAN = 200


def _tope(nombre: str, por_defecto: int) -> int:
    crudo = (os.environ.get(nombre) or "").strip()
    if not crudo:
        return por_defecto
    try:
        valor = int(crudo)
    except ValueError:
        return por_defecto
    return valor if valor > 0 else por_defecto


MAX_ADJUNTOS = _tope("MAX_ADJUNTOS_POR_CORREO", 15)
MAX_PAGINAS = _tope("MAX_PAGINAS_POR_CORREO", 40)
MAX_MEGABYTES = _tope("MAX_MEGABYTES_POR_CORREO", 25)


@dataclass
class Volumen:
    """Lo que trae el correo, y si se pasa de lo que se procesa."""

    adjuntos: int = 0
    paginas: int = 0
    megabytes: float = 0.0
    excesos: List[str] = None

    def __post_init__(self):
        if self.excesos is None:
            self.excesos = []

    @property
    def se_pasa(self) -> bool:
        return bool(self.excesos)

    def detalle(self) -> str:
        """Que se paso y de cuanto, para el informe y para quien lo revise."""
        return "; ".join(self.excesos)

    def resumen(self) -> str:
        return (f"{self.adjuntos} adjunto(s), {self.paginas} pagina(s), "
                f"{self.megabytes:.1f} MB")


def _paginas_de(datos: bytes, nombre: str) -> int:
    """
    Paginas de un adjunto, sin convertirlo.

    Contar paginas abre el documento pero no rasteriza ni reconoce nada, asi
    que cuesta milisegundos: es lo que permite decidir ANTES de pagar el OCR.
    """
    from pipeline.conversion import detectar_tipo

    tipo = detectar_tipo(datos, nombre)
    if tipo == "imagen":
        return 1
    if tipo != "pdf":
        return 0
    try:
        import pymupdf
    except ImportError:
        return 0
    try:
        with pymupdf.open(stream=datos, filetype="pdf") as doc:
            if getattr(doc, "needs_pass", False) and not doc.authenticate(""):
                # No se pudo abrir: no se sabe cuantas paginas tiene, y suponer
                # muchas lo mandaria a revision manual por el motivo equivocado.
                return 0
            return min(doc.page_count, LIMITE_PAGINAS_QUE_SE_CUENTAN)
    except Exception:
        # Un documento que no se puede abrir no se juzga por su volumen: ya
        # tiene su propio motivo en la conversion.
        return 0


def medir(adjuntos: Optional[Sequence[Tuple[str, bytes]]]) -> Volumen:
    """
    Mide el correo por sus adjuntos, descartando los decorativos.

    Las firmas y los logotipos no se cuentan: no llegan al conversor y contarlos
    sacaria de la ia correos que no traen nada.
    """
    from pipeline.materializar import es_ruido

    utiles: List[Tuple[str, bytes]] = [
        (n, d) for n, d in (adjuntos or []) if d and not es_ruido(n)
    ]
    octetos = sum(len(d) for _, d in utiles)
    paginas = sum(_paginas_de(d, n) for n, d in utiles)
    v = Volumen(adjuntos=len(utiles), paginas=paginas,
                megabytes=octetos / 1048576)

    if v.adjuntos > MAX_ADJUNTOS:
        v.excesos.append(f"{v.adjuntos} adjuntos, el tope es {MAX_ADJUNTOS}")
    if v.paginas > MAX_PAGINAS:
        v.excesos.append(f"{v.paginas} paginas, el tope es {MAX_PAGINAS}")
    if v.megabytes > MAX_MEGABYTES:
        v.excesos.append(
            f"{v.megabytes:.1f} MB, el tope es {MAX_MEGABYTES} MB")
    return v
