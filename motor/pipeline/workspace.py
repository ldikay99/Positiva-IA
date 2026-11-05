"""
Espacio de trabajo efimero por correo.

Deja en disco lo que el correo produjo, para poder auditar que leyo la IA y
reprocesar sin repetir el OCR.

Estructura:

    <raiz>/<id_correo>/
        manifest.json           inventario de todo lo materializado
        correo.md               cuerpo del correo actual
        hilo.md                 mensajes anteriores, atribuidos
        adjuntos/001-nombre.md  cada adjunto convertido a Markdown
        extraccion.json         lo que resolvieron los algoritmos
        resolucion.json         lo que resolvio la IA
        validacion.json         veredicto de la compuerta final

Se usa como gestor de contexto para que la limpieza este garantizada incluso
si el procesamiento revienta:

    with Workspace.crear(id_correo) as ws:
        ws.escribir_correo(cuerpo_md)
        ...
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# Estados posibles de un archivo materializado. Explicitos para que un fallo
# de conversion quede registrado en lugar de desaparecer.
ESTADO_OK = "convertido"
ESTADO_VACIO = "sin_contenido"
ESTADO_NO_SOPORTADO = "tipo_no_soportado"
ESTADO_ERROR = "error_conversion"
ESTADO_DEMASIADO_GRANDE = "excede_limite"


@dataclass
class EntradaManifiesto:
    """Un archivo materializado en el workspace."""

    nombre_original: str
    ruta_md: str
    tipo_declarado: str = ""      # lo que decia la extension o el mime
    tipo_real: str = ""           # lo que dicen los bytes
    bytes_originales: int = 0
    sha256: str = ""
    estado: str = ESTADO_OK
    detalle: str = ""             # motivo cuando el estado no es OK
    caracteres_md: int = 0
    fragmentos: int = 1
    ocr_aplicado: bool = False


@dataclass
class Manifiesto:
    """Inventario completo de lo que produjo un correo."""

    id_correo: str
    asunto: str = ""
    remitente_nombre: str = ""
    remitente_correo: str = ""
    entradas: List[EntradaManifiesto] = field(default_factory=list)
    avisos: List[str] = field(default_factory=list)

    def agregar(self, entrada: EntradaManifiesto) -> None:
        self.entradas.append(entrada)

    def avisar(self, mensaje: str) -> None:
        """Registra un fallo que no detiene el procesamiento."""
        if mensaje not in self.avisos:
            self.avisos.append(mensaje)

    @property
    def convertidos(self) -> List[EntradaManifiesto]:
        return [e for e in self.entradas if e.estado == ESTADO_OK]

    @property
    def fallidos(self) -> List[EntradaManifiesto]:
        return [e for e in self.entradas if e.estado not in (ESTADO_OK, ESTADO_VACIO)]

    def a_dict(self) -> Dict[str, Any]:
        return {
            "id_correo": self.id_correo,
            "asunto": self.asunto,
            "remitente_nombre": self.remitente_nombre,
            "remitente_correo": self.remitente_correo,
            "total_entradas": len(self.entradas),
            "convertidos": len(self.convertidos),
            "fallidos": len(self.fallidos),
            "avisos": self.avisos,
            "entradas": [asdict(e) for e in self.entradas],
        }


class Workspace:
    """
    Directorio efimero de trabajo para un correo.

    No se instancia directamente: usar `crear`, que garantiza un directorio
    limpio y aislado.
    """

    def __init__(self, raiz: Path, id_correo: str) -> None:
        self.raiz = Path(raiz)
        self.id_correo = id_correo
        self.manifiesto = Manifiesto(id_correo=id_correo)
        self._temporal = False
        self.dir_adjuntos = self.raiz / "adjuntos"

    # ── Ciclo de vida ──

    @classmethod
    def crear(
        cls,
        id_correo: str,
        base: Optional[Path] = None,
        temporal: bool = True,
    ) -> "Workspace":
        """
        Crea el workspace. Si `base` es None se usa un temporal del sistema.

        `temporal=True` significa que se borra al salir del contexto. Se pone
        en False para poder inspeccionar el resultado durante el desarrollo.
        """
        seguro = nombre_de_directorio(id_correo)
        if base is None:
            raiz = Path(tempfile.mkdtemp(prefix=f"pqrd-{seguro[:24]}-"))
            temporal = True
        else:
            raiz = Path(base) / seguro
            raiz.mkdir(parents=True, exist_ok=True)
        ws = cls(raiz, id_correo)
        ws._temporal = temporal
        ws.dir_adjuntos.mkdir(parents=True, exist_ok=True)
        return ws

    @property
    def es_temporal(self) -> bool:
        """Si `cerrar` borra el directorio con todo lo que tenga dentro."""
        return self._temporal

    def __enter__(self) -> "Workspace":
        return self

    def __exit__(self, *_excepcion) -> None:
        self.cerrar()

    def cerrar(self) -> None:
        """Escribe el manifiesto (siempre, incluso si fallo) y limpia."""
        try:
            self.escribir_manifiesto()
        finally:
            if self._temporal and self.raiz.exists():
                shutil.rmtree(self.raiz, ignore_errors=True)

    # ── Escritura ──

    def escribir_correo(self, markdown: str) -> Path:
        return self._escribir(self.raiz / "correo.md", markdown)

    def escribir_hilo(self, markdown: str) -> Path:
        return self._escribir(self.raiz / "hilo.md", markdown)

    def escribir_adjunto(self, indice: int, nombre: str, markdown: str) -> Path:
        destino = self.dir_adjuntos / f"{indice:03d}-{_slug(nombre)}.md"
        return self._escribir(destino, markdown)

    def escribir_json(self, nombre: str, datos: Any) -> Path:
        destino = self.raiz / nombre
        destino.write_text(
            json.dumps(datos, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return destino

    def escribir_manifiesto(self) -> Path:
        return self.escribir_json("manifest.json", self.manifiesto.a_dict())

    def _escribir(self, destino: Path, contenido: str) -> Path:
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(contenido or "", encoding="utf-8")
        return destino

    # ── Lectura, que es lo que consumiran las herramientas MCP ──

    def listar(self) -> List[Dict[str, Any]]:
        """Inventario sin contenido, para que la IA decida que leer."""
        salida: List[Dict[str, Any]] = []
        for nombre, ruta in (("correo", self.raiz / "correo.md"),
                             ("hilo", self.raiz / "hilo.md")):
            if ruta.exists():
                texto = ruta.read_text(encoding="utf-8")
                salida.append({
                    "id": nombre, "tipo": nombre,
                    "caracteres": len(texto),
                    "lineas": texto.count("\n") + 1,
                })
        for entrada in self.manifiesto.entradas:
            salida.append({
                "id": Path(entrada.ruta_md).stem,
                "tipo": "adjunto",
                "nombre_original": entrada.nombre_original,
                "caracteres": entrada.caracteres_md,
                "estado": entrada.estado,
                "ocr": entrada.ocr_aplicado,
            })
        return salida

    def leer(self, id_documento: str, desde: int = 0, hasta: int = 0) -> str:
        """
        Lee un documento por rango de lineas.

        El rango existe para controlar el gasto: la IA pide el fragmento que
        necesita en vez de recibir el documento completo, que es exactamente
        lo que hace caro el pipeline actual.
        """
        ruta = self._resolver(id_documento)
        if ruta is None or not ruta.exists():
            raise FileNotFoundError(f"documento no encontrado: {id_documento}")
        lineas = ruta.read_text(encoding="utf-8").splitlines()
        if hasta <= 0:
            hasta = len(lineas)
        desde = max(0, desde)
        return "\n".join(lineas[desde:hasta])

    def buscar(self, patron: str, contexto: int = 1) -> List[Dict[str, Any]]:
        """
        Busca en todos los documentos y devuelve ubicaciones con contexto.

        Cada resultado trae documento, numero de linea y las lineas vecinas.
        """
        try:
            regex = re.compile(patron, re.IGNORECASE)
        except re.error as e:
            raise ValueError(f"patron invalido: {e}") from e
        resultados: List[Dict[str, Any]] = []
        for doc in self.listar():
            ruta = self._resolver(doc["id"])
            if ruta is None or not ruta.exists():
                continue
            lineas = ruta.read_text(encoding="utf-8").splitlines()
            for n, linea in enumerate(lineas):
                if regex.search(linea):
                    ini, fin = max(0, n - contexto), min(len(lineas), n + contexto + 1)
                    resultados.append({
                        "documento": doc["id"],
                        "linea": n,
                        "coincidencia": linea.strip()[:200],
                        "contexto": "\n".join(lineas[ini:fin]),
                    })
        return resultados

    def _resolver(self, id_documento: str) -> Optional[Path]:
        if id_documento in ("correo", "correo.md"):
            return self.raiz / "correo.md"
        if id_documento in ("hilo", "hilo.md"):
            return self.raiz / "hilo.md"
        candidato = self.dir_adjuntos / f"{id_documento}.md"
        if candidato.exists():
            return candidato
        for ruta in self.dir_adjuntos.glob("*.md"):
            if ruta.stem == id_documento or id_documento in ruta.stem:
                return ruta
        return None

    def texto_completo(self) -> str:
        """
        Todo el contenido concatenado. Solo para la linea base comparativa:
        el pipeline no debe mandar todo al modelo.
        """
        partes: List[str] = []
        for doc in self.listar():
            ruta = self._resolver(doc["id"])
            if ruta and ruta.exists():
                partes.append(ruta.read_text(encoding="utf-8"))
        return "\n\n".join(partes)


def sha256_de(datos: bytes) -> str:
    return hashlib.sha256(datos).hexdigest()


# Cuanto del identificador se conserva legible en el nombre del directorio, y
# cuanto del resumen se le pega para distinguirlo.
_LARGO_LEGIBLE = 40
_LARGO_DEL_RESUMEN = 16


def nombre_de_directorio(id_correo: str) -> str:
    """
    Nombre de carpeta unico por correo.

    El identificador solo no sirve: los de Microsoft Graph del mismo buzon
    miden mas de ciento cincuenta caracteres y coinciden en casi todos, asi que
    recortarlos da la misma carpeta para todos los correos de la bandeja, y
    entonces cada uno pisa los documentos del anterior. Se le pega un resumen
    del identificador COMPLETO, que si depende de la parte que los distingue.
    """
    completo = str(id_correo or "")
    resumen = hashlib.sha256(completo.encode("utf-8")).hexdigest()[:_LARGO_DEL_RESUMEN]
    legible = _slug(completo)[:_LARGO_LEGIBLE].strip("-._")
    return f"{legible}-{resumen}" if legible else resumen


def _slug(nombre: str) -> str:
    """
    Nombre seguro para el sistema de archivos.

    Se normaliza para quitar acentos y se limita la longitud: los nombres de
    adjunto reales traen tildes, espacios, parentesis y a veces 200 caracteres.
    """
    base = unicodedata.normalize("NFKD", nombre or "")
    base = "".join(c for c in base if not unicodedata.combining(c))
    limpio = "".join(c if (c.isalnum() or c in "-_.") else "-" for c in base)
    while "--" in limpio:
        limpio = limpio.replace("--", "-")
    limpio = limpio.strip("-._").lower()
    return (limpio[:80] or "documento")
