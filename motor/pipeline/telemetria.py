"""
Telemetria por ejecucion y por dia: atribucion algoritmo/IA/registro/manual,
costo del modelo y motivos de revision manual.

    logs/ejecuciones/<AAAA-MM-DD>/<HHMMSS>-<id>.txt
    logs/diario/<AAAA-MM-DD>.txt
    logs/serie.tsv        para graficar la tendencia

`logs/` esta excluido de git: contiene datos de casos reales.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pipeline.atribucion import Origen
from pipeline.costo import Contador

# Columnas de "quien resolvio cada campo", en el orden en que se imprimen. Tiene
# que cubrir TODOS los valores de `Origen`: una columna de menos no da error,
# deja los porcentajes sin sumar cien y el campo que falta no aparece en ninguna
# parte. Hay una prueba que lo comprueba.
_ORIGENES_DEL_INFORME: Tuple[Origen, ...] = (
    Origen.ALGORITMO, Origen.IA, Origen.REGISTRO, Origen.NO_RESUELTO,
)
# "no_resuelto" se titula "manual" porque eso es lo que le pasa al correo.
_NOMBRE_DEL_ORIGEN: Dict[Origen, str] = {
    Origen.ALGORITMO: "algoritmo", Origen.IA: "IA",
    Origen.REGISTRO: "registro", Origen.NO_RESUELTO: "manual",
}
# Cada celda es "  1234  100%": siete de cuenta, espacio, cuatro de porcentaje
# y el simbolo. La cabecera se alinea con eso.
_ANCHO_DE_COLUMNA = 13


def _en_un_renglon(texto: str) -> str:
    """
    El texto sin saltos de linea, para que un aviso no parta el informe.

    Un aviso puede traer el cuerpo de una respuesta HTTP: la pagina de desafio
    de Cloudflare son varias lineas de HTML, y con sus saltos dentro el informe
    dejaba de leerse como un informe.
    """
    return " ".join(str(texto or "").split())


def _cierre_de(c: "RegistroCorreo") -> str:
    """
    Como termino el correo, sin repetir el desenlace que ya va en su columna.

    Un desenlace que no es radicado tampoco es siempre revision manual: un
    duplicado, un reenvio o un correo sin reclamar salian descritos como
    "a revision manual por sin motivo", que no es lo que les paso.
    """
    if c.accion == "radicado":
        return f"radicado {c.radicado}"
    if c.accion == "manual":
        return f"a revision manual por {c.motivo_manual or 'sin motivo registrado'}"
    if c.accion == "reenviado":
        return f"a {c.area_destino}" if c.area_destino else "sin area registrada"
    if c.motivo_manual:
        return f"motivo {c.motivo_manual}"
    return ""


def referencia(id_correo: str) -> str:
    """
    Identificador corto que distingue un correo de otro en el informe.

    Los identificadores de Graph del mismo buzon coinciden en casi todo su
    largo y se diferencian al final: cortarlos por el principio da la misma
    cadena para todos los correos de la corrida.
    """
    limpio = str(id_correo or "").rstrip("=")
    return limpio[-10:] if limpio else "?"

# Columnas de la serie historica, en orden. Una fila por corrida.
CABECERA_DE_LA_SERIE = (
    "fecha_hora", "id", "correos", "radicados", "manual",
    "pct_algoritmo", "pct_ia", "pct_registro", "pct_manual",
    "llamadas_modelo", "tokens_entrada", "costo_usd",
)

# Motivos de revision manual. Mas finos que las tres etiquetas de Outlook, que
# no permiten distinguir la causa.
MOTIVOS_MANUAL = {
    "SIN_NOMBRE_REMITENTE": "no se pudo determinar el nombre de quien escribe",
    "SIN_NOMBRE_AFECTADO": "no se pudo determinar el nombre del afectado",
    "SIN_DOCUMENTO": "no se pudo determinar el documento de identidad",
    "DOCUMENTO_AMBIGUO": "varios documentos candidatos sin forma de desambiguar",
    "DOCUMENTO_DE_OTRA_PERSONA": "el registro tiene ese documento a nombre de "
                                 "otra persona que la del correo",
    "EMPRESA_CON_DOCUMENTO_DE_PERSONA": "el nombre es una razon social y el "
                                        "documento es de una persona natural",
    "SIN_TIPO_SOLICITUD": "no se pudo clasificar en ninguna de las 27 categorias",
    "SOLO_EMPRESA_SIN_AFECTADO": "el correo identifica una empresa por su NIT y "
                                 "ninguna persona natural",
    "ADJUNTO_PROTEGIDO": "el adjunto con el soporte tiene contraseña",
    "ANEXO_DE_RADICADO": "es del hilo de un radicado y trae documentos que ese "
                         "radicado no tiene",
    "POSIBLE_SOLICITUD_NUEVA": "es del hilo de un radicado de la misma persona "
                               "pero pide algo distinto de lo radicado",
    "ADJUNTO_ILEGIBLE": "el adjunto no se pudo convertir",
    "ADJUNTO_NO_LEIDO": "un adjunto en un formato que no se convierte",
    "SIN_OCR": "imagen sin motor de OCR disponible",
    "VALIDACION_RECHAZO": "la compuerta final rechazo los datos por incoherentes",
    "INYECCION_DETECTADA": "se detecto un intento de manipular al modelo",
    "CLOUDFLARE_403": "Cloudflare devolvio un captcha al radicar",
    "SGDEA_CAIDO": "el backend de radicacion no respondio",
    "PETICION_MAL_FORMADA": "el backend rechazo la forma de la peticion de "
                            "radicacion",
    "RADICACION_SIN_CONFIRMAR": "la radicacion se acepto sin devolver numero: "
                                "buscar el caso en SGDEA antes de radicarlo "
                                "de nuevo",
    "MODELO_NO_RESPONDIO": "el modelo no respondio a tiempo o no estaba disponible",
    "MODELO_SIN_PERMISO": "el modelo rechazo la peticion por autorizacion",
    "FACTURA_ELECTRONICA": "el correo es una factura, no una PQRD",
    "REMITENTE_NO_IDENTIFICABLE": "buzon interno sin remitente externo en el hilo",
    "REMITENTE_EXCLUIDO": "lo escribe un buzon que no radica por este canal",
    "RECLAMO_AGOTADO": "el pipeline se cayo varias veces con este mismo correo",
    "TIEMPO_DE_CORRIDA_AGOTADO": "la corrida se quedo sin tiempo con este correo "
                                 "a medias",
    "ERROR_INESPERADO": "fallo no clasificado, revisar la traza",
    "VOLUMEN_EXCESIVO": "el correo trae mas adjuntos, paginas o tamaño de lo "
                        "que se procesa automaticamente",
}

# Motivos cuyo desenlace NO lo decidio el contenido del correo.
#
# La frase del agente dice que dato falto y por que no estaba en la
# comunicacion. Con estos motivos ese dato NO es la causa: el correo no se
# radico porque el backend no respondio, porque la peticion salio mal formada o
# porque la corrida se quedo sin tiempo. Presentar esa frase como el motivo
# afirma algo que no es —se vio un correo aplazado por el backend cuyo "por
# que" hablaba de una direccion que faltaba—, y va tanto al informe como al
# correo de aviso interno.
#
# ADJUNTO_ILEGIBLE, SIN_OCR y ADJUNTO_PROTEGIDO quedan FUERA a proposito: ahi el
# dato que falto si es la causa y la frase del agente lo explica.
MOTIVOS_AJENOS_AL_CONTENIDO = frozenset({
    "SGDEA_CAIDO",
    "CLOUDFLARE_403",
    "PETICION_MAL_FORMADA",
    "RADICACION_SIN_CONFIRMAR",
    "MODELO_NO_RESPONDIO",
    "MODELO_SIN_PERMISO",
    "RECLAMO_AGOTADO",
    "TIEMPO_DE_CORRIDA_AGOTADO",
    "ERROR_INESPERADO",
})


def lo_decidio_el_contenido(motivo: str) -> bool:
    """Si el desenlace lo decidio lo que decia el correo y no la infraestructura."""
    return str(motivo or "").strip().upper() not in MOTIVOS_AJENOS_AL_CONTENIDO


# El unico desenlace que una frase sobre un dato que falto puede explicar. La
# escribe el agente al cerrar y dice que NO encontro, asi que solo tiene sentido
# donde eso fue la causa.
DESENLACE_QUE_EXPLICA_LA_FRASE = "manual"


def explica_el_desenlace(accion: str, motivo: str) -> bool:
    """
    Si la frase del agente explica como termino ESTE correo.

    En un correo radicado no explica nada: el dato que el agente no encontro no
    impidio nada, y la frase se escribe antes de que el registro de personas
    rellene y antes de la compuerta, asi que puede nombrar un campo que para
    cuando se radico ya estaba resuelto. Publicarla ahi presenta como problema
    un correo que salio bien.
    """
    return (str(accion or "").strip() == DESENLACE_QUE_EXPLICA_LA_FRASE
            and lo_decidio_el_contenido(motivo))


@dataclass
class RegistroCorreo:
    """Lo que se registra de un correo procesado."""

    id_correo: str
    asunto: str = ""
    accion: str = ""              # radicado | duplicado | reenviado | aplazado
                                  # | manual | fallido
    radicado: str = ""
    motivo_manual: str = ""
    # Area interna a la que se remitio, y en que se reconocio que le
    # correspondia. Solo con `accion == "reenviado"`.
    area_destino: str = ""
    razon_del_reenvio: str = ""
    # En una frase, que dato falto y por que. La escribe el agente sobre ESTE
    # correo; el motivo es una etiqueta del catalogo, igual para todos.
    explicacion: str = ""
    duracion_ms: int = 0
    # etapa -> milisegundos, para desglosar la duracion total del correo.
    etapas_ms: Dict[str, int] = field(default_factory=dict)
    # campo -> Origen
    atribucion: Dict[str, Origen] = field(default_factory=dict)
    avisos: List[str] = field(default_factory=list)

    def cuenta(self, origen: Origen) -> int:
        return sum(1 for o in self.atribucion.values() if o is origen)


@dataclass
class Ejecucion:
    """
    Una corrida del job. Acumula y escribe los informes.

    Se construye con la marca de tiempo explicita para que las pruebas puedan
    fijarla: un informe que depende del reloj no se puede comparar.
    """

    id_ejecucion: str
    inicio: datetime
    raiz_logs: Path
    correos: List[RegistroCorreo] = field(default_factory=list)
    contador: Contador = field(default_factory=Contador)

    # ── Registro ──

    def registrar(self, registro: RegistroCorreo) -> None:
        self.correos.append(registro)
        self.contador.correos = len(self.correos)

    # ── Agregados ──

    @property
    def total(self) -> int:
        return len(self.correos)

    def por_accion(self) -> Dict[str, int]:
        d: Dict[str, int] = {}
        for c in self.correos:
            d[c.accion or "sin_accion"] = d.get(c.accion or "sin_accion", 0) + 1
        return d

    def atribucion_por_campo(self) -> Dict[str, Dict[Origen, int]]:
        d: Dict[str, Dict[Origen, int]] = {}
        for c in self.correos:
            for campo, origen in c.atribucion.items():
                fila = d.setdefault(campo, {o: 0 for o in Origen})
                fila[origen] += 1
        return d

    def totales_atribucion(self) -> Dict[Origen, int]:
        t = {o: 0 for o in Origen}
        for fila in self.atribucion_por_campo().values():
            for o, n in fila.items():
                t[o] += n
        return t

    def resumen(self) -> Dict[str, Any]:
        """
        Los numeros de la corrida, cada uno en su campo.

        Hace la telemetria consultable: el informe en texto se lee de a una
        corrida, y estos campos permiten filtrar y comparar varias desde el
        explorador de logs.
        """
        acciones = self.por_accion()
        tot = self.totales_atribucion()
        campos = sum(tot.values()) or 1
        datos: Dict[str, Any] = {
            "id_ejecucion": self.id_ejecucion,
            "correos": self.total,
            "radicados": acciones.get("radicado", 0),
            "manual": acciones.get("manual", 0),
            "duplicados": acciones.get("duplicado", 0),
            "reenviados": acciones.get("reenviado", 0),
            "aplazados": acciones.get("aplazado", 0),
            "fallidos": acciones.get("fallido", 0),
            "llamadas_modelo": self.contador.total_llamadas,
            "tokens_entrada": self.contador.total_entrada,
        }
        for origen in _ORIGENES_DEL_INFORME:
            datos[f"pct_{origen.value}"] = tot[origen] * 100 // campos
        if self.contador.costo_usd is not None:
            datos["costo_usd"] = round(self.contador.costo_usd, 6)
        motivos = self.motivos_manual()
        if motivos:
            datos["motivos_manual"] = motivos
        return datos

    def motivos_manual(self) -> Dict[str, int]:
        d: Dict[str, int] = {}
        for c in self.correos:
            if c.accion == "manual" and c.motivo_manual:
                d[c.motivo_manual] = d.get(c.motivo_manual, 0) + 1
        return d

    # ── Informes ──

    def tiempo_por_etapa(self) -> Dict[str, int]:
        """Milisegundos acumulados por etapa en toda la corrida."""
        total: Dict[str, int] = {}
        for r in self.correos:
            for etapa, ms in r.etapas_ms.items():
                total[etapa] = total.get(etapa, 0) + ms
        return total

    def _lineas_de_tiempo(self) -> List[str]:
        """
        Desglose del tiempo, que es lo unico que dice donde optimizar.

        El porcentaje se calcula sobre la suma de las etapas, no sobre la
        duracion total: lo que no se mide queda como "sin medir" en vez de
        repartirse entre las etapas y hacerlas parecer mas lentas.
        """
        por_etapa = self.tiempo_por_etapa()
        if not por_etapa:
            return []
        medido = sum(por_etapa.values()) or 1
        total = self.total or 1
        lineas = [
            "",
            "DONDE SE VA EL TIEMPO",
            f"{'':<26}{'total':>10}{'por correo':>14}{'del medido':>12}",
        ]
        for etapa, ms in sorted(por_etapa.items(), key=lambda kv: -kv[1]):
            lineas.append(
                f"  {etapa:<24}{ms / 1000:>8.1f}s{ms / total / 1000:>12.1f}s"
                f"{ms * 100 // medido:>11}%"
            )
        sin_medir = sum(r.duracion_ms for r in self.correos) - medido
        if sin_medir > 0:
            lineas.append(
                f"  {'(sin medir)':<24}{sin_medir / 1000:>8.1f}s"
                f"{sin_medir / total / 1000:>12.1f}s")
        return lineas

    # Cuantas clases de aviso distintas se listan en el informe.
    MAX_CLASES_DE_AVISO = 20

    # Cuanto del aviso de ejemplo se conserva. La causa de un fallo va al final
    # —el tipo de excepcion y su mensaje— y cortar antes deja el informe
    # diciendo que algo fallo sin decir que.
    LARGO_DEL_EJEMPLO = 300

    @staticmethod
    def _clase_de_aviso(aviso: str) -> str:
        """
        Reduce un aviso a su tipo, para poder contarlos.

        Corta en el primer detalle variable (dos puntos, parentesis o flecha)
        para que el nombre de un archivo o un id no genere una clase por
        correo.
        """
        corte = len(aviso)
        for separador in (": ", " (", " -> "):
            i = aviso.find(separador)
            if 0 < i < corte:
                corte = i
        return aviso[:corte].strip() or aviso[:60]

    def _lineas_de_avisos(self) -> List[str]:
        """
        Avisos agrupados por clase, con cuantos correos afecta cada uno y un
        ejemplo. Las clases que no caben se cuentan en la ultima linea.
        """
        grupos: Dict[str, List[Tuple[str, str]]] = {}
        for c in self.correos:
            for aviso in c.avisos:
                grupos.setdefault(self._clase_de_aviso(aviso), []).append(
                    (c.id_correo, aviso))
        if not grupos:
            return []

        total = sum(len(v) for v in grupos.values())
        lineas = ["", f"AVISOS ({total} en {len(grupos)} clases)"]
        ordenados = sorted(grupos.items(), key=lambda kv: -len(kv[1]))
        for clase, ocurrencias in ordenados[: self.MAX_CLASES_DE_AVISO]:
            correos = len({c for c, _ in ocurrencias})
            lineas.append(f"  {len(ocurrencias):>3}x  {_en_un_renglon(clase)[:80]}")
            id_correo, ejemplo = ocurrencias[0]
            lineas.append(
                f"       en {correos} correo(s), p.ej. {referencia(id_correo)}: "
                f"{_en_un_renglon(ejemplo)[:self.LARGO_DEL_EJEMPLO]}"
            )
        omitidas = len(ordenados) - self.MAX_CLASES_DE_AVISO
        if omitidas > 0:
            lineas.append(
                f"  ... y {omitidas} clase(s) mas, en el detalle por correo")
        return lineas

    def informe(self) -> str:
        fin = datetime.now()
        dur = (fin - self.inicio).total_seconds()
        total = self.total or 1
        # El informe sale una vez por corrida, cuarenta y ocho veces al dia.
        # La leyenda de como se lee y como se interpretan las cifras es la misma
        # en todas y esta en el documento de arquitectura: repetirla aqui son
        # diecisiete renglones fijos por delante de los numeros de ESTA corrida.
        lineas = [
            f"EJECUCION {self.inicio.isoformat(timespec='seconds')}  "
            f"id={self.id_ejecucion}  duracion={dur:.1f}s",
            "-" * 68,
            f"CORREOS            {self.total}",
        ]
        for accion, n in sorted(self.por_accion().items(), key=lambda kv: -kv[1]):
            lineas.append(f"  {accion:<17}{n:>4}   ({n * 100 / total:5.1f}%)")

        atrib = self.atribucion_por_campo()
        if atrib:
            lineas += [
                "",
                "QUIEN RESOLVIO CADA CAMPO",
                f"{'':<27}" + "".join(
                    f"{_NOMBRE_DEL_ORIGEN[o]:>{_ANCHO_DE_COLUMNA}}"
                    for o in _ORIGENES_DEL_INFORME
                ),
            ]

            def _fila(etiqueta: str, cuenta: Dict[Origen, int]) -> str:
                # El total sale de la suma y no de una lista de origenes, para
                # que añadir uno nuevo no deje los porcentajes sin sumar 100.
                t = sum(cuenta.values()) or 1
                celdas = "".join(
                    f"{cuenta[o]:>7} {cuenta[o] * 100 // t:>4}%"
                    for o in _ORIGENES_DEL_INFORME
                )
                return f"  {etiqueta:<25}{celdas}"

            for campo in sorted(atrib):
                lineas.append(_fila(campo, atrib[campo]))
            tot = self.totales_atribucion()
            lineas += ["-" * 68, _fila("TOTAL CAMPOS", tot)]

        lineas += self._lineas_de_tiempo()
        lineas += ["", self.contador.informe()]

        motivos = self.motivos_manual()
        if motivos:
            lineas += ["", f"REVISION MANUAL ({sum(motivos.values())})"]
            for motivo, n in sorted(motivos.items(), key=lambda kv: -kv[1]):
                desc = MOTIVOS_MANUAL.get(motivo, "motivo no catalogado")
                lineas.append(f"  {n:>3}  {motivo:<28} {desc}")
            # Separado porque un fallo de red no mide el desempeño de la IA.
            infra = {"CLOUDFLARE_403", "SGDEA_CAIDO", "SIN_OCR", "ADJUNTO_ILEGIBLE",
                     "MODELO_NO_RESPONDIO", "MODELO_SIN_PERMISO"}
            por_infra = sum(n for m, n in motivos.items() if m in infra)
            if por_infra:
                lineas.append(
                    f"  De esos, {por_infra} son por infraestructura y no por "
                    f"desempeño de la IA."
                )

        if self.correos:
            lineas += ["", "DETALLE POR CORREO"]
            for c in self.correos:
                alg = c.cuenta(Origen.ALGORITMO)
                ia = c.cuenta(Origen.IA)
                man = c.cuenta(Origen.NO_RESUELTO)
                cierre = _cierre_de(c)
                lineas.append(
                    f"  {referencia(c.id_correo):<12} {c.accion:<9} "
                    f"algoritmo={alg:<2} IA={ia:<2} manual={man:<2} "
                    f"{c.duracion_ms/1000:>5.1f}s  {cierre}"
                )
                if c.asunto:
                    lineas.append(
                        f"  {'':<12} asunto: "
                        f"{_en_un_renglon(c.asunto)[:70]}")
                # La frase del agente solo se muestra si el desenlace lo decidio
                # el contenido. Con un motivo de infraestructura habla de un
                # dato que falto, que no es la causa de que el correo no se
                # radicara.
                if c.razon_del_reenvio:
                    lineas.append(
                        f"  {'':<12} por que: "
                        f"{_en_un_renglon(c.razon_del_reenvio)[:120]}")
                if c.explicacion and explica_el_desenlace(
                        c.accion, c.motivo_manual):
                    lineas.append(
                        f"  {'':<12} por que: "
                        f"{_en_un_renglon(c.explicacion)[:120]}")
                faltantes = [
                    campo for campo, o in sorted(c.atribucion.items())
                    if o is Origen.NO_RESUELTO
                ]
                if faltantes:
                    lineas.append(f"  {'':<12} sin resolver: {', '.join(faltantes)}")

        lineas += self._lineas_de_avisos()
        return "\n".join(lineas)

    def escribir(self) -> Dict[str, Path]:
        """
        Escribe el informe de la corrida, actualiza el diario y la serie.

        Devuelve las rutas escritas para poder verificarlas.
        """
        from pipeline import almacen

        dia = self.inicio.strftime("%Y-%m-%d")
        hora = self.inicio.strftime("%H%M%S")
        informe = self.informe()

        ruta_ejec = almacen.unir(
            self.raiz_logs, "ejecuciones", dia, f"{hora}-{self.id_ejecucion}.txt")
        almacen.escribir(ruta_ejec, informe + "\n")

        ruta_diario = almacen.unir(self.raiz_logs, "diario", f"{dia}.txt")
        almacen.anexar(ruta_diario, "\n" + "=" * 68 + "\n" + informe + "\n")

        ruta_serie = almacen.unir(self.raiz_logs, "serie.tsv")
        tot = self.totales_atribucion()
        gran = sum(tot.values()) or 1
        acciones = self.por_accion()
        costo = self.contador.costo_usd
        fila = "\t".join([
            self.inicio.isoformat(timespec="seconds"),
            self.id_ejecucion,
            str(self.total),
            str(acciones.get("radicado", 0)),
            str(acciones.get("manual", 0)),
            str(tot[Origen.ALGORITMO] * 100 // gran),
            str(tot[Origen.IA] * 100 // gran),
            str(tot[Origen.REGISTRO] * 100 // gran),
            str(tot[Origen.NO_RESUELTO] * 100 // gran),
            str(self.contador.total_llamadas),
            str(self.contador.total_entrada),
            f"{costo:.6f}" if costo is not None else "",
        ]) + "\n"
        # La cabecera NO se escribe: la conoce quien lee. Decidirlo aqui exigia
        # leer el destino primero, y en el bucket leer necesita permiso de
        # listar, que la corrida no tiene por que necesitar para escribir.
        almacen.anexar(ruta_serie, fila)

        return {"ejecucion": ruta_ejec, "diario": ruta_diario, "serie": ruta_serie}


def nueva_ejecucion(
    raiz_logs: Path,
    id_ejecucion: Optional[str] = None,
    inicio: Optional[datetime] = None,
) -> Ejecucion:
    """
    Crea una ejecucion. `inicio` se puede fijar para que las pruebas sean estables.

    `raiz_logs` admite una ruta de disco o una direccion `gs://`. La segunda no
    se convierte a `Path`: en Windows quedaria como `gs:\\bucket` y dejaria de
    ser una direccion.
    """
    from pipeline import almacen

    momento = inicio or datetime.now()
    ident = id_ejecucion or momento.strftime("%f")
    raiz = str(raiz_logs) if almacen.es_bucket(raiz_logs) else Path(raiz_logs)
    return Ejecucion(id_ejecucion=ident, inicio=momento, raiz_logs=raiz)


def leer_serie(raiz_logs: Path) -> List[Dict[str, Any]]:
    """
    Lee la serie historica para ver la tendencia.

    Cada fila es una corrida, con la atribucion por origen y el consumo del
    modelo: es la vista que permite comparar corridas entre si.
    """
    from pipeline import almacen

    contenido = almacen.leer(almacen.unir(raiz_logs, "serie.tsv"))
    if contenido is None:
        return []
    cabecera = list(CABECERA_DE_LA_SERIE)
    filas: List[Dict[str, Any]] = []
    for linea in contenido.splitlines():
        valores = linea.split("\t")
        # Las corridas escriben solo la fila de datos. Las series de antes
        # llevaban la cabecera en la primera linea y siguen leyendose.
        if valores and valores[0] == CABECERA_DE_LA_SERIE[0]:
            cabecera = valores
            continue
        if len(valores) == len(cabecera):
            filas.append(dict(zip(cabecera, valores)))
    return filas
