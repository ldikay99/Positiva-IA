"""
El registro de proveedores: cada módulo de `proveedores/` define un adaptador y
lo registra con su id. Agregar uno nuevo es escribir una clase con `llamar` (y,
si se puede, `listar`) y llamar a `registrar(MiAdaptador())`.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from proveedores.base import ErrorDeNegocio, Peticion, Resultado


class Adaptador:
    """Lo que implementa cada proveedor."""

    id: str = ""
    nombre: str = ""
    descripcion: str = ""
    autenticacion: str = ""
    tipo_credencial: Optional[str] = None  # None: no necesita (Vertex usa la identidad del servicio)
    requiere_credencial: bool = True
    # Cómo pide JSON con esquema: «nativo» (el servicio lo garantiza), «objeto» (modo JSON + el esquema en
    # las instrucciones) o «instruido» (solo instrucciones). En los dos últimos se valida y se repara una vez.
    json_modo: str = "nativo"
    # Lo que acepta un modelo que no está en el catálogo (None: no se sabe).
    por_defecto: Dict[str, Optional[bool]] = {}
    # Si se le pueden listar los modelos con su API.
    lista_en_vivo: bool = False

    def llamar(self, p: Peticion, cred: dict, flujo: str) -> Resultado:  # pragma: no cover - abstracto
        raise NotImplementedError

    def listar(self, cred: dict, flujo: str, timeout: float) -> List[dict]:
        return []

    def revisar_credencial(self, cred: dict) -> None:
        if self.requiere_credencial and not (cred or {}).get("api_key"):
            raise ErrorDeNegocio(f"{self.nombre}: la credencial no tiene api_key")

    def describir(self) -> dict:
        return {"id": self.id, "nombre": self.nombre, "descripcion": self.descripcion, "autenticacion": self.autenticacion,
                "tipo_credencial": self.tipo_credencial, "requiere_credencial": self.requiere_credencial,
                "json": self.json_modo, "lista_en_vivo": self.lista_en_vivo}


PROVEEDORES: Dict[str, Adaptador] = {}


def registrar(a: Adaptador) -> Adaptador:
    if not a.id or a.id in PROVEEDORES:
        raise ValueError(f"proveedor sin id o repetido: {a.id!r}")
    PROVEEDORES[a.id] = a
    return a


def obtener(proveedor: str) -> Adaptador:
    a = PROVEEDORES.get(str(proveedor or ""))
    if not a:
        raise ErrorDeNegocio(f"proveedor de IA desconocido «{proveedor}» (válidos: {', '.join(PROVEEDORES)})")
    return a
