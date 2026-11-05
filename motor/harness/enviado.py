"""
Acierto de lo que REALMENTE llega a SGDEA, no de lo que resuelve el extractor.

    python -m harness.enviado
    python -m harness.enviado --campo tipo_remitente

Entre el extractor y SGDEA hay dos capas que cambian valores: la compuerta
corrige y el radicador rellena con valores por defecto. Aqui se arma el payload
caso por caso y se compara contra las etiquetas del corpus.

El payload se arma SIEMPRE, tambien cuando la compuerta rechazaria el correo:
sin modelo ningun caso se radica, asi que medir solo los aprobados no mediria
nada. El numero de rechazos se informa aparte.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from harness.atribucion import coincide
from harness.caso import Caso, cargar_corpus, extraer_de
from models.email import EmailAddress, EmailBody, EmailMessage
from pipeline import compuerta
from pipeline.compuerta import Veredicto
from pipeline.radicador import SEXO_POR_DEFECTO, armar_payload

RAIZ_CORPUS = Path(__file__).resolve().parent.parent / "corpus" / "casos"
ANCHO = 80

# Campo del corpus -> clave del payload que SGDEA recibe.
CAMPO_EN_EL_PAYLOAD: Dict[str, str] = {
    "tipo_remitente": "datosRemitenteTipoRemitenteId",
    "tipo_documento_afectado": "datosAfectadoTipoDocumentoId",
    "numero_documento_afectado": "datosAfectadoNumeroDocumento",
    "nombre_afectado": "datosAfectadoNombre",
    "tipo_documento_remitente": "datosRemitenteTipoDocumentoId",
    "numero_documento_remitente": "datosRemitenteNumeroDocumento",
    "nombre_remitente": "datosRemitenteNombre",
    "tipo_pqrd": "solicitudTipoPqrdId",
    "tipo_solicitud": "solicitudTipoSolicitudId",
    "oficina": "oficina",
    "sexo_afectado": "datosAfectadoGeneroId",
    "telefono_afectado": "datosAfectadoTelefonoCelular",
    "direccion_afectado": "datosAfectadoDireccion",
}


def _correo_de(caso: Caso) -> EmailMessage:
    """El caso del corpus como el `EmailMessage` que recibe el radicador."""
    return EmailMessage(
        id=caso.id,
        subject=caso.asunto,
        body=EmailBody(content=caso.cuerpo_md),
        from_address=EmailAddress(name=caso.remitente_nombre,
                                 address=caso.remitente_correo),
        received_datetime="2026-08-25T12:00:00Z",
    )


# Valores con los que el payload dice "no se sabe" en vez de afirmar un dato.
# El 3 de sexo es literalmente "Otro o no determinado" en el catalogo.
ABSTENCIONES = {
    "sexo_afectado": {SEXO_POR_DEFECTO, str(SEXO_POR_DEFECTO)},
}


def _es_abstencion(campo: str, valor: object) -> bool:
    """Si el valor enviado no afirma ningun dato sobre la persona."""
    if valor in (None, ""):
        return True
    return valor in ABSTENCIONES.get(campo, set())


@dataclass
class Marca:
    """Conteos de un campo sobre lo enviado."""

    comparable: int = 0
    acierto: int = 0
    # De los comparables, los que el radicador relleno por su cuenta.
    por_defecto: int = 0
    acierto_del_defecto: int = 0
    # Del defecto: los que AFIRMAN un dato equivocado, frente a los que se
    # abstienen. Afirmar mal escribe un dato falso sobre una persona;
    # abstenerse solo deja el campo sin resolver, y queda anotado.
    afirma_mal: int = 0
    se_abstiene: int = 0

    @property
    def porcentaje(self) -> Optional[int]:
        if not self.comparable:
            return None
        return self.acierto * 100 // self.comparable

    @property
    def porcentaje_del_defecto(self) -> Optional[int]:
        if not self.por_defecto:
            return None
        return self.acierto_del_defecto * 100 // self.por_defecto


@dataclass
class Fallo:
    caso_id: str
    campo: str
    enviado: str
    esperado: str
    fue_defecto: bool
    abstencion: bool = False


@dataclass
class Enviado:
    casos: int = 0
    rechazados: int = 0
    por_campo: Dict[str, Marca] = field(default_factory=dict)
    fallos: List[Fallo] = field(default_factory=list)

    def medir(self, caso: Caso) -> None:
        self.casos += 1
        hallazgos = extraer_de(caso).hallazgos
        dictamen = compuerta.validar(hallazgos)
        if dictamen.veredicto is Veredicto.RECHAZADO:
            self.rechazados += 1
        # Las correcciones de la compuerta se aplican tambien cuando el veredicto
        # es de rechazo: son las que cambian el valor que se enviaria.
        hallazgos = compuerta.aplicar(hallazgos, dictamen)

        suposiciones: List[str] = []
        payload = armar_payload(
            _correo_de(caso), hallazgos, caso.cuerpo_md,
            suposiciones=suposiciones,
        ).to_form_data()
        # Un campo aparece en `suposiciones` cuando el radicador lo relleno.
        supuestos = {s.split()[0] for s in suposiciones}

        for campo, clave in CAMPO_EN_EL_PAYLOAD.items():
            esperado = getattr(caso.esperado, campo, None)
            if esperado is None:
                continue
            marca = self.por_campo.setdefault(campo, Marca())
            enviado = payload.get(clave)
            fue_defecto = campo in supuestos
            marca.comparable += 1
            if fue_defecto:
                marca.por_defecto += 1
            if coincide(enviado, esperado, campo):
                marca.acierto += 1
                if fue_defecto:
                    marca.acierto_del_defecto += 1
                continue
            # El fallo se clasifica por el VALOR enviado, no por si quedo
            # anotado: `_valor` no anota cuando el defecto es vacio, asi que
            # contar solo lo anotado esconde las abstenciones.
            abstencion = _es_abstencion(campo, enviado)
            if abstencion:
                marca.se_abstiene += 1
            else:
                marca.afirma_mal += 1
            self.fallos.append(Fallo(
                caso_id=caso.id, campo=campo, enviado=str(enviado),
                esperado=str(esperado), fue_defecto=fue_defecto,
                abstencion=abstencion))

    def informe(self, campo: Optional[str] = None) -> str:
        lineas = [
            f"ACIERTO DE LO QUE SE ENVIA A SGDEA  ({self.casos} casos; sin el "
            f"modelo la compuerta rechazaria {self.rechazados})",
            "=" * ANCHO,
            f"{'CAMPO':<26}{'COMPARA':>8}{'ACIERTA':>8}{'%':>6}"
            f"{'DEFECTO':>9}{'OK':>5}{'AFIRMA MAL':>12}{'ABSTIENE':>10}",
            "-" * ANCHO,
        ]
        for nombre in CAMPO_EN_EL_PAYLOAD:
            if campo and nombre != campo:
                continue
            m = self.por_campo.get(nombre)
            if m is None:
                continue
            pct = "    -" if m.porcentaje is None else f"{m.porcentaje:>4}%"
            lineas.append(
                f"{nombre:<26}{m.comparable:>8}{m.acierto:>8}{pct:>6}"
                f"{m.por_defecto:>9}{m.acierto_del_defecto:>5}"
                f"{m.afirma_mal:>12}{m.se_abstiene:>10}")

        def suma(atributo: str) -> int:
            return sum(getattr(m, atributo) for m in self.por_campo.values())

        comparables, aciertos = suma("comparable"), suma("acierto")
        total = f"{aciertos * 100 // comparables:>4}%" if comparables else "    -"
        lineas += [
            "-" * ANCHO,
            f"{'TOTAL':<26}{comparables:>8}{aciertos:>8}{total:>6}"
            f"{suma('por_defecto'):>9}{suma('acierto_del_defecto'):>5}"
            f"{suma('afirma_mal'):>12}{suma('se_abstiene'):>10}",
            "",
            "DEFECTO    = de los comparables, los que el radicador relleno por su",
            "             cuenta, y cuantos de ellos acertaron (OK).",
            "AFIRMA MAL = el defecto escribe un dato equivocado sobre la persona.",
            "ABSTIENE   = el defecto deja el campo vacio o 'no determinado'. No es",
            "             un dato falso, y el radicador lo anota en suposiciones.",
        ]

        fallos = [f for f in self.fallos if not campo or f.campo == campo]
        lineas += ["", f"FALLOS ({len(fallos)})", "-" * ANCHO]
        if not fallos:
            lineas.append("  ninguno")
        for f in sorted(fallos, key=lambda f: (f.campo, f.caso_id)):
            marca = "  [por defecto]" if f.fue_defecto else ""
            lineas.append(f"  {f.campo:<26} {f.caso_id:<11}{marca}")
            lineas.append(f"      envia {f.enviado!r}   esperaba {f.esperado!r}")
        return "\n".join(lineas)


def correr(raiz: Path = RAIZ_CORPUS) -> Optional[Enviado]:
    casos = cargar_corpus(raiz)
    if not casos:
        return None
    resultado = Enviado()
    for caso in casos:
        resultado.medir(caso)
    return resultado


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--campo", choices=sorted(CAMPO_EN_EL_PAYLOAD), default=None)
    args = p.parse_args()

    resultado = correr()
    if resultado is None:
        print("Corpus vacio. Correr primero: python -m harness.recolectar")
        return 1
    print(resultado.informe(campo=args.campo))
    fallos = [f for f in resultado.fallos
              if not args.campo or f.campo == args.campo]
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
