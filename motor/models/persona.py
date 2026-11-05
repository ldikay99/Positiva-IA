"""
Modelos de persona/empresa
"""

from dataclasses import dataclass, field
from typing import Optional, List, Any


@dataclass
class Pais:
    """Pais"""

    id_pais: int = 425
    nombre: str = "Colombia"
    id_continente: Optional[int] = None
    id_positiva: Optional[int] = None


@dataclass
class Departamento:
    """Departamento"""

    id_departamento: Optional[int] = None
    nombre: str = ""
    id_pais: int = 425
    codigo_divipola: Optional[str] = None


@dataclass
class Municipio:
    """Municipio"""

    id_municipio: Optional[int] = None
    nombre: str = ""
    id_departamento: Optional[int] = None
    codigo_divipola: Optional[str] = None


@dataclass
class PersonaData:
    """Datos de persona o empresa"""

    id: Optional[int] = None
    tipo_documento: str = "CC"
    numero_documento: str = ""
    pais: Pais = field(default_factory=Pais)
    departamento: Departamento = field(default_factory=Departamento)
    municipio: Municipio = field(default_factory=Municipio)
    nombre1: str = ""
    nombre2: Optional[str] = None
    apellido1: str = ""
    apellido2: Optional[str] = None
    telefono: str = ""
    direccion: str = ""
    email: str = ""
    genero: Optional[int] = None
    siniestros: List[Any] = field(default_factory=list)

    @property
    def nombre_completo(self) -> str:
        """Retorna el nombre completo"""
        partes = [self.nombre1, self.nombre2, self.apellido1, self.apellido2]
        return " ".join(p for p in partes if p).strip()

    @classmethod
    def from_extracted_data(
        cls,
        numero_documento: str,
        tipo_documento: str = "CC",
        nombre: str = "",
    ) -> "PersonaData":
        """Crea PersonaData a partir de los datos extraidos del correo.

        Sin nombre los campos quedan vacios: el numero de documento nunca se
        usa como nombre. Quien radica debe comprobar `nombre_completo`.

        La geografia queda sin resolver. Fijarla aqui la afirma como leida del
        correo; el relleno por defecto lo aplica y lo anota `pipeline.radicador`.
        """
        # Se asume el orden NOMBRE1 NOMBRE2 APELLIDO1 APELLIDO2.
        partes = nombre.split() if nombre else []
        nombre1 = ""
        nombre2 = None
        apellido1 = ""
        apellido2 = None

        if len(partes) >= 4:
            nombre1 = partes[0]
            nombre2 = partes[1]
            apellido1 = partes[2]
            apellido2 = " ".join(partes[3:])
        elif len(partes) == 3:
            nombre1 = partes[0]
            apellido1 = partes[1]
            apellido2 = partes[2]
        elif len(partes) == 2:
            nombre1 = partes[0]
            apellido1 = partes[1]
        elif len(partes) == 1:
            nombre1 = partes[0]

        return cls(
            id=None,
            tipo_documento=tipo_documento,
            numero_documento=numero_documento,
            pais=Pais(id_pais=425, nombre="Colombia"),
            departamento=Departamento(),
            municipio=Municipio(),
            nombre1=nombre1,
            nombre2=nombre2,
            apellido1=apellido1,
            apellido2=apellido2,
            telefono="",
            direccion="",
            email="",
            genero=None,
            siniestros=[],
        )
