"""
Mapeo de departamentos y municipios de Colombia
"""
import httpx
from typing import Optional, Dict, Tuple

# Mapeo de nombre de departamento → ID (normalizado a mayúsculas sin tildes)
DEPARTAMENTOS_MAP: Dict[str, int] = {
    "AMAZONAS": 1,
    "ANTIOQUIA": 2,
    "ARAUCA": 3,
    "ARCHIPIELAGO DE SAN ANDRES": 4,
    "SAN ANDRES": 4,
    "ATLANTICO": 5,
    "BOGOTA": 6,
    "BOGOTA D.C.": 6,
    "BOGOTA DC": 6,
    "D.C.": 6,
    "BOLIVAR": 7,
    "BOYACA": 8,
    "CALDAS": 9,
    "CAQUETA": 10,
    "CASANARE": 11,
    "CAUCA": 12,
    "CESAR": 13,
    "CHOCO": 14,
    "CORDOBA": 15,
    "CUNDINAMARCA": 16,
    "GUAINIA": 17,
    "GUAVIARE": 18,
    "HUILA": 19,
    "LA GUAJIRA": 20,
    "GUAJIRA": 20,
    "MAGDALENA": 21,
    "META": 22,
    "NARINO": 23,
    "NORTE DE SANTANDER": 24,
    "PUTUMAYO": 25,
    "QUINDIO": 26,
    "RISARALDA": 27,
    "SANTANDER": 28,
    "SUCRE": 29,
    "TOLIMA": 30,
    "VALLE DEL CAUCA": 31,
    "VALLE": 31,
    "VAUPES": 32,
    "VICHADA": 33,
}

# Municipio capital por defecto para cada departamento
CAPITAL_MUNICIPIO: Dict[int, int] = {
    1: 1089,   # Amazonas → Leticia
    2: 1,      # Antioquia → Medellín
    3: 1048,   # Arauca → Arauca
    4: 1087,   # San Andrés → San Andrés
    5: 126,    # Atlántico → Barranquilla
    6: 149,    # Bogotá → Bogotá D.C.
    7: 150,    # Bolívar → Cartagena
    8: 196,    # Boyacá → Tunja
    9: 320,    # Caldas → Manizales
    10: 347,   # Caquetá → Florencia
    11: 1055,  # Casanare → Yopal
    12: 363,   # Cauca → Popayán
    13: 404,   # Cesar → Valledupar
    14: 575,   # Chocó → Quibdó
    15: 429,   # Córdoba → Montería
    16: 149,   # Cundinamarca → Bogotá D.C.
    17: 1100,  # Guainía → Inírida
    18: 1108,  # Guaviare → San José del Guaviare
    19: 605,   # Huila → Neiva
    20: 642,   # La Guajira → Riohacha
    21: 657,   # Magdalena → Santa Marta
    22: 687,   # Meta → Villavicencio
    23: 716,   # Nariño → Pasto
    24: 780,   # Norte de Santander → Cúcuta
    25: 1074,  # Putumayo → Mocoa
    26: 820,   # Quindío → Armenia
    27: 832,   # Risaralda → Pereira
    28: 846,   # Santander → Bucaramanga
    29: 933,   # Sucre → Sincelejo
    30: 959,   # Tolima → Ibagué
    31: 1006,  # Valle del Cauca → Cali
    32: 1112,  # Vaupés → Mitú
    33: 1118,  # Vichada → Puerto Carreño
}


def normalizar_texto(texto: str) -> str:
    """Normaliza texto para comparación (mayúsculas, sin tildes)"""
    if not texto:
        return ""
    # Reemplazar tildes
    replacements = {
        'Á': 'A', 'É': 'E', 'Í': 'I', 'Ó': 'O', 'Ú': 'U',
        'á': 'A', 'é': 'E', 'í': 'I', 'ó': 'O', 'ú': 'U',
        'Ñ': 'N', 'ñ': 'N', 'Ü': 'U', 'ü': 'U',
    }
    resultado = texto.upper()
    for orig, repl in replacements.items():
        resultado = resultado.replace(orig, repl)
    return resultado.strip()


def get_departamento_id(nombre_o_id) -> Optional[int]:
    """Obtiene el ID del departamento desde nombre o ID directo"""
    if nombre_o_id is None:
        return None

    # Si ya es un número, devolverlo
    if isinstance(nombre_o_id, (int, float)):
        return int(nombre_o_id)

    # Si es string, buscar en el mapeo
    nombre_normalizado = normalizar_texto(str(nombre_o_id))
    return DEPARTAMENTOS_MAP.get(nombre_normalizado)


def get_municipio_capital(departamento_id: int) -> Optional[int]:
    """Obtiene el municipio capital de un departamento"""
    return CAPITAL_MUNICIPIO.get(departamento_id)


async def consultar_municipios_por_departamento(departamento_id: int, token: str) -> list:
    """Consulta los municipios de un departamento via API"""
    try:
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"https://ms-admin-prd.proyectos-3t.tech/municipios/list/{departamento_id}",
                headers=headers,
            )

        if response.status_code >= 400:
            return []

        return response.json()
    except Exception:
        return []


def buscar_municipio_por_nombre(municipios: list, nombre_municipio: str) -> Optional[int]:
    """Busca un municipio por nombre en una lista de municipios"""
    if not municipios or not nombre_municipio:
        return None

    nombre_normalizado = normalizar_texto(nombre_municipio)

    for mun in municipios:
        nombre_mun = normalizar_texto(mun.get("nombre", ""))
        if nombre_mun == nombre_normalizado:
            return mun.get("idMunicipio")
        # Buscar coincidencia parcial
        if nombre_normalizado in nombre_mun or nombre_mun in nombre_normalizado:
            return mun.get("idMunicipio")

    return None


