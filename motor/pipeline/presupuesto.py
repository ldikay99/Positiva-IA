"""
Cuanto tiempo tiene una corrida antes de que empiece la siguiente.

El job arranca cada 30 minutos. Una corrida que se pase de esa ventana se
solapa con la siguiente, y entonces dos procesos miran la misma bandeja: es la
situacion en la que un correo puede trabajarse dos veces.

El presupuesto acota la corrida entera, no cada llamada. Un correo que espera
tres veces por un backend caido consume tiempo que ya no tienen los que vienen
detras, y sin una cuenta comun cada espera parece razonable por separado.

Quedarse sin presupuesto NO es un fallo: los correos que no se llegaron a tomar
siguen en la bandeja, sin marca, y la siguiente corrida los ve igual que si
acabaran de llegar.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

# Ventana entre corridas del job.
CADENCIA_S = 30 * 60

# Cuanto de la ventana se usa. El resto es margen: la corrida tiene que caber
# con holgura, no llegar justa al arranque de la siguiente.
FRACCION_DE_LA_VENTANA = 0.66

# Lo que se aparta para cerrar servicios y escribir la telemetria. Sin esta
# reserva, la corrida se queda sin tiempo justo antes de dejar constancia de lo
# que hizo.
RESERVA_S = 60.0

# Por debajo de esto no se empieza un correo nuevo: no da tiempo ni a
# convertir los adjuntos, y dejarlo a medias obliga a rescatarlo despues.
MINIMO_POR_CORREO_S = 45.0


@dataclass
class Presupuesto:
    """
    El tiempo que le queda a la corrida.

    `total_s` es la corrida completa, reserva incluida. `agotado` responde
    contando ya la reserva, de modo que quien lo consulta no tiene que
    acordarse de restarla.
    """

    total_s: float = CADENCIA_S * FRACCION_DE_LA_VENTANA
    reserva_s: float = RESERVA_S
    inicio: float = field(default_factory=time.monotonic)

    def transcurrido(self) -> float:
        return time.monotonic() - self.inicio

    def queda(self) -> float:
        """Segundos utiles, sin contar la reserva. Nunca negativo."""
        return max(0.0, self.total_s - self.reserva_s - self.transcurrido())

    def agotado(self) -> bool:
        """Si ya no da tiempo a empezar otro correo."""
        return self.queda() < MINIMO_POR_CORREO_S

    def alcanza_para(self, segundos: float) -> bool:
        """Si cabe una espera de `segundos` sin comerse la reserva."""
        return self.queda() >= segundos

    def recortar(self, espera: float) -> float:
        """
        La espera que si cabe.

        Devuelve 0 cuando no cabe ninguna: esperar hasta agotar el presupuesto
        deja el correo sin radicar Y sin tiempo para los siguientes, que es el
        peor de los dos resultados.
        """
        if espera <= 0:
            return 0.0
        disponible = self.queda() - MINIMO_POR_CORREO_S
        if disponible <= 0:
            return 0.0
        return min(espera, disponible)

    def informe(self) -> str:
        return (f"presupuesto {self.total_s / 60:.0f} min, "
                f"usado {self.transcurrido() / 60:.1f} min, "
                f"queda {self.queda() / 60:.1f} min")
