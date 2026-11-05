"""Contabilidad de consumo del modelo: llamadas, tokens y costo por corrida."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Tarifas de Vertex AI por millon de tokens, en USD.
# Declaradas explicitamente para que el calculo sea auditable y actualizable.
# Si el modelo usado no esta en la tabla, el costo se reporta como None en lugar
# de inventar un numero.
TARIFAS_USD_POR_MILLON: Dict[str, Dict[str, float]] = {
    "gemini-2.5-flash": {"entrada": 0.30, "salida": 2.50},
    "gemini-2.5-pro": {"entrada": 1.25, "salida": 10.00},
    "gemini-2.0-flash": {"entrada": 0.15, "salida": 0.60},
}
TARIFAS_VIGENTES_AL = "2026-08-24"


@dataclass
class Llamada:
    """
    El consumo de un paso del pipeline.

    Un registro puede cubrir varias invocaciones al modelo: el agente resuelve
    un correo en varias vueltas y se anota una sola vez, con los tokens ya
    sumados. `turnos` es cuantas vueltas cubre, y es lo que se suma para saber
    cuantas veces se llamo al modelo.
    """

    etiqueta: str          # que agente o paso la hizo
    modelo: str
    tokens_entrada: int = 0
    tokens_salida: int = 0
    duracion_ms: int = 0
    error: str = ""
    turnos: int = 1

    @property
    def costo_usd(self) -> Optional[float]:
        tarifa = TARIFAS_USD_POR_MILLON.get(self.modelo)
        if not tarifa:
            return None
        return (
            self.tokens_entrada / 1_000_000 * tarifa["entrada"]
            + self.tokens_salida / 1_000_000 * tarifa["salida"]
        )


@dataclass
class Contador:
    """
    Acumula el consumo de una corrida.

    Se instancia una vez por ejecucion y se le registran las llamadas. Sin
    estado global: cada instancia es independiente.
    """

    llamadas: List[Llamada] = field(default_factory=list)
    correos: int = 0

    def registrar(
        self,
        etiqueta: str,
        modelo: str,
        tokens_entrada: int = 0,
        tokens_salida: int = 0,
        duracion_ms: int = 0,
        error: str = "",
        turnos: int = 1,
    ) -> None:
        self.llamadas.append(
            Llamada(etiqueta, modelo, tokens_entrada, tokens_salida,
                    duracion_ms, error, turnos)
        )

    def registrar_respuesta(self, etiqueta: str, modelo: str, respuesta) -> None:
        """
        Registra desde una respuesta de google-genai, leyendo usage_metadata.

        Tolera que el campo no venga: en ese caso queda en cero y se ve en el
        informe como llamada sin metricas, que es mejor que estimar.
        """
        entrada = salida = 0
        meta = getattr(respuesta, "usage_metadata", None)
        if meta is not None:
            entrada = getattr(meta, "prompt_token_count", 0) or 0
            salida = getattr(meta, "candidates_token_count", 0) or 0
        self.registrar(etiqueta, modelo, entrada, salida)

    # ── Agregados ──

    @property
    def total_llamadas(self) -> int:
        """Invocaciones al modelo, no registros: un registro puede valer varias."""
        return sum(l.turnos for l in self.llamadas)

    @property
    def total_entrada(self) -> int:
        return sum(l.tokens_entrada for l in self.llamadas)

    @property
    def total_salida(self) -> int:
        return sum(l.tokens_salida for l in self.llamadas)

    @property
    def total_errores(self) -> int:
        return sum(1 for l in self.llamadas if l.error)

    @property
    def costo_usd(self) -> Optional[float]:
        """None si algun modelo usado no tiene tarifa conocida."""
        if not self.llamadas:
            return 0.0
        parciales = [l.costo_usd for l in self.llamadas]
        if any(p is None for p in parciales):
            return None
        return sum(p for p in parciales if p is not None)

    @property
    def costo_por_correo(self) -> Optional[float]:
        total = self.costo_usd
        if total is None or self.correos <= 0:
            return None
        return total / self.correos

    @property
    def llamadas_por_correo(self) -> float:
        return self.total_llamadas / self.correos if self.correos else 0.0

    def por_modelo(self) -> Dict[str, Dict[str, int]]:
        agrupado: Dict[str, Dict[str, int]] = {}
        for l in self.llamadas:
            d = agrupado.setdefault(
                l.modelo, {"llamadas": 0, "entrada": 0, "salida": 0}
            )
            d["llamadas"] += l.turnos
            d["entrada"] += l.tokens_entrada
            d["salida"] += l.tokens_salida
        return agrupado

    def por_etiqueta(self) -> Dict[str, Dict[str, int]]:
        agrupado: Dict[str, Dict[str, int]] = {}
        for l in self.llamadas:
            d = agrupado.setdefault(
                l.etiqueta, {"llamadas": 0, "entrada": 0, "salida": 0}
            )
            d["llamadas"] += l.turnos
            d["entrada"] += l.tokens_entrada
            d["salida"] += l.tokens_salida
        return agrupado

    def informe(self) -> str:
        lineas = [
            f"CONSUMO DEL MODELO   (tarifas vigentes al {TARIFAS_VIGENTES_AL})",
            "-" * 64,
            f"  correos procesados      {self.correos}",
            f"  llamadas al modelo      {self.total_llamadas}"
            f"   ({self.llamadas_por_correo:.1f} por correo)",
            f"  tokens de entrada       {self.total_entrada:,}",
            f"  tokens de salida        {self.total_salida:,}",
        ]
        if self.total_errores:
            lineas.append(f"  llamadas con error      {self.total_errores}")
        costo = self.costo_usd
        if costo is None:
            lineas.append("  costo                   no calculable (modelo sin tarifa)")
        else:
            lineas.append(f"  costo total             USD {costo:.6f}")
            por_correo = self.costo_por_correo
            if por_correo is not None:
                lineas.append(f"  costo por correo        USD {por_correo:.6f}")
        if self.por_modelo():
            lineas.append("")
            lineas.append("  POR MODELO")
            for modelo, d in sorted(self.por_modelo().items()):
                lineas.append(
                    f"    {modelo:<24} {d['llamadas']:>4} llamadas"
                    f"  {d['entrada']:>9,} ent  {d['salida']:>7,} sal"
                )
        if self.por_etiqueta():
            lineas.append("")
            lineas.append("  POR PASO")
            for etiqueta, d in sorted(
                self.por_etiqueta().items(), key=lambda kv: -kv[1]["entrada"]
            ):
                lineas.append(
                    f"    {etiqueta:<24} {d['llamadas']:>4} llamadas"
                    f"  {d['entrada']:>9,} ent  {d['salida']:>7,} sal"
                )
        return "\n".join(lineas)
