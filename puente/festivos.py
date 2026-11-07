"""
Días festivos de Colombia, calculados sin red (Ley 51 de 1983, «Ley Emiliani»).

- Fijos: 1 de enero, 1 de mayo, 20 de julio, 7 de agosto, 8 y 25 de diciembre.
- Se pasan al lunes siguiente: Reyes (6 ene), San José (19 mar), San Pedro y
  San Pablo (29 jun), Asunción (15 ago), Día de la Raza (12 oct), Todos los
  Santos (1 nov) e Independencia de Cartagena (11 nov).
- Según la Pascua (algoritmo anónimo gregoriano): Jueves y Viernes Santo, y
  pasados al lunes la Ascensión (+43 días), Corpus Christi (+64) y el Sagrado
  Corazón (+71).

`dia_habil` y `siguiente_apertura` son lo que usa el nodo ventana_horario.
"""
from __future__ import annotations

import datetime
from functools import lru_cache
from typing import Dict, Iterable, Optional, Tuple

DIAS = ("lun", "mar", "mie", "jue", "vie", "sab", "dom")
NOMBRES_DIAS = {"lun": "lunes", "mar": "martes", "mie": "miércoles", "jue": "jueves", "vie": "viernes", "sab": "sábado", "dom": "domingo"}


def pascua(anio: int) -> datetime.date:
    """El domingo de Pascua (algoritmo anónimo gregoriano, Meeus/Jones/Butcher)."""
    a = anio % 19
    b, c = divmod(anio, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    mes, dia = divmod(h + l_ - 7 * m + 114, 31)
    return datetime.date(anio, mes, dia + 1)


def _lunes_siguiente(d: datetime.date) -> datetime.date:
    return d + datetime.timedelta(days=(7 - d.weekday()) % 7)


@lru_cache(maxsize=64)
def festivos(anio: int) -> Dict[datetime.date, str]:
    """{fecha: nombre} de los festivos de Colombia en ese año."""
    r: Dict[datetime.date, str] = {}

    def poner(d: datetime.date, nombre: str) -> None:
        r[d] = f"{r[d]} y {nombre}" if d in r else nombre

    for (mes, dia), nombre in {(1, 1): "Año Nuevo", (5, 1): "Día del Trabajo", (7, 20): "Día de la Independencia",
                               (8, 7): "Batalla de Boyacá", (12, 8): "Inmaculada Concepción", (12, 25): "Navidad"}.items():
        poner(datetime.date(anio, mes, dia), nombre)
    for (mes, dia), nombre in {(1, 6): "Día de los Reyes Magos", (3, 19): "Día de San José", (6, 29): "San Pedro y San Pablo",
                               (8, 15): "La Asunción de la Virgen", (10, 12): "Día de la Raza", (11, 1): "Todos los Santos",
                               (11, 11): "Independencia de Cartagena"}.items():
        poner(_lunes_siguiente(datetime.date(anio, mes, dia)), nombre)
    p = pascua(anio)
    poner(p - datetime.timedelta(days=3), "Jueves Santo")
    poner(p - datetime.timedelta(days=2), "Viernes Santo")
    poner(p + datetime.timedelta(days=43), "Ascensión del Señor")
    poner(p + datetime.timedelta(days=64), "Corpus Christi")
    poner(p + datetime.timedelta(days=71), "Sagrado Corazón de Jesús")
    return dict(sorted(r.items()))


def es_festivo(d: datetime.date) -> Optional[str]:
    """El nombre del festivo de ese día, o None."""
    return festivos(d.year).get(d)


def _minutos(hhmm: str) -> int:
    h, m = str(hhmm).strip().split(":")
    return int(h) * 60 + int(m)


def dia_habil(d: datetime.date, dias: Iterable[str] = DIAS[:5], con_festivos: bool = True) -> bool:
    return DIAS[d.weekday()] in set(dias) and not (con_festivos and es_festivo(d))


def dentro(ahora: datetime.datetime, dias: Iterable[str], desde: str, hasta: str, con_festivos: bool = True) -> bool:
    """Si `ahora` (con su zona) cae dentro de la ventana: un día permitido, no festivo (si se pide), entre desde y hasta."""
    dias = list(dias)
    if not dia_habil(ahora.date(), dias, con_festivos):
        return False
    m = ahora.hour * 60 + ahora.minute
    return _minutos(desde) <= m < _minutos(hasta)


def siguiente_apertura(ahora: datetime.datetime, dias: Iterable[str], desde: str, hasta: str,
                       con_festivos: bool = True, max_dias: int = 60) -> Optional[datetime.datetime]:
    """Cuándo se abre la ventana la próxima vez (ahora mismo si ya está dentro). None si no abre en `max_dias`."""
    dias = list(dias)
    if dentro(ahora, dias, desde, hasta, con_festivos):
        return ahora
    a, b = _minutos(desde), _minutos(hasta)
    for k in range(0, max_dias + 1):
        d = ahora.date() + datetime.timedelta(days=k)
        if not dia_habil(d, dias, con_festivos):
            continue
        inicio = ahora.replace(year=d.year, month=d.month, day=d.day, hour=a // 60, minute=a % 60, second=0, microsecond=0)
        if inicio > ahora:
            return inicio
        if k == 0 and ahora.hour * 60 + ahora.minute < b:  # hoy, ya pasó el inicio pero no el fin (no se llega: estaría dentro)
            return ahora
    return None


def describir(dias: Iterable[str], desde: str, hasta: str, con_festivos: bool) -> Tuple[str, str]:
    """(días en palabras, horario) para los mensajes."""
    dias = list(dias)
    if dias == list(DIAS[:5]):
        txt = "de lunes a viernes"
    elif dias == list(DIAS[:6]):
        txt = "de lunes a sábado"
    else:
        txt = ", ".join(NOMBRES_DIAS[x] for x in dias)
    return txt + (", sin festivos" if con_festivos else ""), f"de {desde} a {hasta}"
