#!/usr/bin/env python3
"""
Punto de entrada del pipeline nuevo.

    python correr.py                 procesa el lote y escribe telemetria
    python correr.py --n 5           limita a 5 correos
    python correr.py --seco          no radica ni mueve correo

Con MODO_TEST=true no se escribe nada en PRD.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict

from pipeline.salida import AVISO, ERROR, INFO, emitir, referencia_del_correo

RAIZ = Path(__file__).resolve().parent


def _raiz_de_logs():
    """
    Donde van los informes: disco o bucket.

    `RUTA_LOGS` admite `gs://bucket/prefijo`. El disco del Job es efimero, asi
    que la serie historica solo se acumula si apunta a un bucket.
    """
    crudo = (os.environ.get("RUTA_LOGS") or "").strip()
    if not crudo:
        return RAIZ / "logs"
    from pipeline.almacen import es_bucket

    return crudo if es_bucket(crudo) else Path(crudo)


LOGS = _raiz_de_logs()
WORKSPACES = Path(os.environ.get("RUTA_WORKSPACE") or RAIZ / "workspace")

# Tope de cada peticion a SGDEA. `radicar_pqrd` reintenta hasta tres veces, asi
# que este valor multiplica: es el techo de lo que un solo correo puede tardar
# en la radicacion.
TIMEOUT_SGDEA_S = 45.0

# Cuantos correos se atienden a la vez. Casi todo el tiempo de un correo es
# espera de red —el modelo, Graph, SGDEA— y en serie la maquina no hace nada
# mientras espera. El tope existe para no disparar el limite de tasa del modelo
# ni abrir mas conexiones de las que el cliente HTTP reusa.
CONCURRENCIA = int(os.environ.get("CONCURRENCIA") or 5)

# Cuantos correos toma cada corrida. El scheduler dispara 48 veces al dia, asi
# que el lote multiplicado por 48 es el techo de lo que la bandeja puede
# desaguar en un dia: por debajo del ritmo de llegada, lo que no se toma se
# represa y la cola crece sin que nada falle. Lo que lo acota por arriba no es
# el lote sino el presupuesto, que corta antes de empezar un correo nuevo. Sale
# del entorno para poder ajustarlo sin desplegar.
CORREOS_POR_CORRIDA = int(os.environ.get("CORREOS_POR_CORRIDA") or 50)

# Tope de UN correo. No lo cubre el presupuesto de la corrida: ese se comprueba
# antes de empezar cada correo, de modo que uno que se queda esperando dentro
# de una llamada no lo consulta nunca y retiene la corrida hasta que el Job se
# agota.
#
# Diez minutos es justo la holgura que el presupuesto deja dentro de la
# cadencia —20 de los 30—, asi que un correo abandonado en el ultimo momento
# hace terminar la corrida cuando arranca la siguiente y no encima de ella. Y
# esta muy por encima del correo mas lento medido, de 4,3 minutos, para que
# solo alcance al que no va a terminar.
TOPE_POR_CORREO_S = float(os.environ.get("TOPE_POR_CORREO_S") or 600)


def _credenciales_de_vertex():
    """
    Credenciales para Vertex, o None para que la libreria resuelva sola.

    En el Cloud Run Job hay ADC —la cuenta de servicio del Job— y no hay
    `gcloud`. En una maquina de desarrollo suele pasar lo contrario. Se intenta
    ADC primero y solo se cae a la CLI si no lo hay, para que el mismo codigo
    valga en los dos sitios sin una bandera que alguien tenga que acordarse de
    poner.
    """
    try:
        import google.auth

        credenciales, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"])
        return credenciales
    except Exception as e:
        print(f"AVISO: sin credenciales por defecto ({type(e).__name__}); "
              f"se intenta la sesion de gcloud")

    try:
        tok = subprocess.run(
            ["gcloud", "auth", "print-access-token"],
            capture_output=True, text=True, shell=True, check=True,
        ).stdout.strip()
    except Exception as e:
        raise RuntimeError(
            f"no hay credenciales para Vertex: ni ADC ni sesion de gcloud "
            f"({type(e).__name__}). En el Job las da la cuenta de servicio; "
            f"en local, `gcloud auth application-default login`."
        ) from e
    if not tok:
        raise RuntimeError(
            "`gcloud auth print-access-token` no devolvio ningun token")
    from google.oauth2.credentials import Credentials

    return Credentials(token=tok)


# Desde cuanto una etapa merece su propia entrada. Por debajo no aporta: el
# tiempo de todas las etapas va igual en el informe del correo, y once entradas
# por correo tapan lo que si hay que ver.
UMBRAL_ETAPA_LENTA_MS = int(os.environ.get("UMBRAL_ETAPA_LENTA_MS") or 5_000)

# Un umbral igual para todas las etapas anuncia como lenta la que SIEMPRE tarda.
# El agente son varias llamadas al modelo y el OCR recorre las paginas de cada
# adjunto: los dos pasan de cinco segundos en el caso normal, asi que con el
# umbral general dejaban una entrada por correo y ninguna significaba nada. Cada
# etapa se compara contra lo que tarda cuando va bien.
UMBRAL_POR_ETAPA_MS: Dict[str, int] = {
    "agente": 30_000,
    "conversion_a_markdown": 20_000,
    "radicacion": 25_000,
    "descarga_adjuntos": 15_000,
}


def umbral_de(etapa: str) -> int:
    """Desde cuantos milisegundos se anuncia esta etapa."""
    return UMBRAL_POR_ETAPA_MS.get(etapa, UMBRAL_ETAPA_LENTA_MS)


# Cuanto del fallo se conserva. Los errores de Google Cloud Storage traen el
# cuerpo JSON de la respuesta, que repite el mismo mensaje tres veces y ocupa mas
# de mil caracteres.
LARGO_DEL_FALLO = 300


def en_una_linea(fallo: str) -> str:
    """
    El fallo en un renglon, sin el cuerpo de la respuesta.

    Un fallo de varias lineas se convierte en varias entradas de Cloud Logging y
    deja de poder leerse como un fallo.
    """
    return " ".join(str(fallo or "").split())[:LARGO_DEL_FALLO]


def _paso(correo: str, etapa: str, ms: int, fallo: str = "") -> None:
    """
    Anuncia una etapa que fallo o que tardo.

    Las etapas rapidas no se emiten: su tiempo va igual en el informe del
    correo, y emitirlas todas deja once entradas por correo entre las que no se
    encuentra la que importa. "Rapida" se mide contra lo que tarda ESTA etapa
    cuando va bien, no contra un umbral igual para todas.
    """
    if not fallo and ms < umbral_de(etapa):
        return
    ref = referencia_del_correo(correo)
    emitir(f"  [{ref}] {etapa} ({ms} ms)" + (f" — {fallo}" if fallo else ""),
           severidad=ERROR if fallo else AVISO,
           solo_json=("correo", "etapa", "ms", "fallo"),
           correo=ref, etapa=etapa, ms=ms, fallo=fallo)


def _severidad_de(accion: str) -> str:
    """
    Nivel con el que sale un correo.

    Un correo a revision manual no es un error del sistema —hizo lo correcto al
    no inventarse un dato— pero tiene que poder filtrarse aparte de los que
    salieron bien. Un fallido si es un error: nadie decidio nada sobre el.
    """
    if accion == "fallido":
        return ERROR
    if accion in ("manual", "aplazado", "sin_reclamar"):
        return AVISO
    return INFO


def _severidad_del_lote(resumen: dict) -> str:
    """El lote destaca si algun correo se quedo sin procesar."""
    if resumen.get("fallidos") or resumen.get("colgados"):
        return ERROR
    if resumen.get("manual") or resumen.get("sin_tomar"):
        return AVISO
    return INFO


def _presupuesto_de_la_corrida():
    """
    Cuanto tiempo tiene esta corrida.

    `PRESUPUESTO_S` lo fija el entorno para poder ajustarlo sin desplegar. Por
    defecto sale de la cadencia del job, que es lo que de verdad lo limita.
    """
    from pipeline.presupuesto import CADENCIA_S, FRACCION_DE_LA_VENTANA, Presupuesto

    crudo = (os.environ.get("PRESUPUESTO_S") or "").strip()
    total = CADENCIA_S * FRACCION_DE_LA_VENTANA
    if crudo:
        try:
            total = float(crudo)
        except ValueError:
            print(f"AVISO: PRESUPUESTO_S='{crudo}' no es un numero; "
                  f"se usa {total:.0f}s")
    return Presupuesto(total_s=total)


def _auditoria():
    """
    Cliente de auditoria para deduplicar por remitente y asunto.

    Si no se puede construir se sigue sin esa capa y el detector lo anota en el
    informe de cada correo.
    """
    try:
        from services.supabase_client import SupabaseService

        return SupabaseService()
    except Exception as e:
        print(f"AVISO: sin auditoria ({type(e).__name__}: {e})")
        return None


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--n", type=int, default=CORREOS_POR_CORRIDA)
    p.add_argument("--seco", action="store_true",
                   help="no radica ni mueve correo")
    p.add_argument("--carpeta", default="inbox", choices=("inbox", "exitosa"))
    p.add_argument("--vertex", action="store_true",
                   help="usa Vertex AI, el unico backend que resuelve campos. "
                        "Cuesta dinero, por eso hay que pedirlo (VERTEX.md)")
    args = p.parse_args()

    try:
        from config import settings
    except Exception as e:
        # `ConfiguracionIncompleta` ya nombra las variables que faltan.
        print(f"\n{e}\n")
        return 2

    from pipeline.modelo import ModeloVertex
    from pipeline.atribucion import Origen
    from pipeline.orquestador import Orquestador
    from pipeline.personas import ConsultaDePersonas
    from pipeline.radicador import Radicador
    from pipeline.telemetria import (
        explica_el_desenlace,
        nueva_ejecucion,
    )
    from services.outlook import OutlookService
    from services.radicacion import RadicacionService

    if not settings.MODO_TEST and args.seco:
        print("--seco exige MODO_TEST=true")
        return 1
    if not settings.MODO_TEST and not args.seco:
        print("ATENCION: MODO_TEST=false. Esto RADICA DE VERDAD en SGDEA.")

    # Cada llamada a Vertex se factura, asi que la bandera es obligatoria: sin
    # ella la corrida se detiene en vez de gastar. No hay backend por defecto
    # precisamente para que ninguna corrida empiece a cobrar por descuido.
    if not args.vertex:
        print("falta --vertex: es el unico backend que resuelve campos, y "
              "cuesta dinero, por eso hay que pedirlo explicitamente.")
        return 1

    modelo = ModeloVertex(
        proyecto=settings.GCP_PROJECT,
        ubicacion="global",
        credenciales=_credenciales_de_vertex(),
        endpoint_global=True,
    )

    ejecucion = nueva_ejecucion(LOGS, inicio=datetime.now())
    presupuesto = _presupuesto_de_la_corrida()
    outlook = OutlookService()
    # Tres intentos por radicacion: con el valor por defecto un solo correo
    # puede consumir varios minutos de la ventana entre corridas.
    servicio_radicacion = RadicacionService(timeout=TIMEOUT_SGDEA_S)
    auditoria = _auditoria()
    # Solo lectura, y va tambien en modo seco: consultar el registro no escribe
    # nada. Que no encuentre a alguien no invalida el documento —puede no estar
    # vinculado— y el pipeline sigue igual.
    registro = ConsultaDePersonas(url_base=settings.MS_SGDEA_BETA_URL)
    orq = Orquestador(
        modelo=modelo,
        outlook=None if args.seco else outlook,
        radicador=None if args.seco else Radicador(servicio=servicio_radicacion),
        base_workspace=str(WORKSPACES),
        ejecucion=ejecucion,
        auditoria=auditoria,
        sgdea=registro,
        traza=_paso,
        presupuesto=presupuesto,
    )

    carpeta = (
        settings.OUTLOOK_FOLDER_ID if args.carpeta == "inbox"
        else settings.OUTLOOK_FOLDER_RADICACION_EXITOSA
    )

    concurrencia = max(1, CONCURRENCIA)
    sin_tomar = 0
    colgados = 0
    try:
        correos = (await outlook.get_emails(carpeta, limit=args.n))[: args.n]
        print(f"Procesando {len(correos)} correo(s)  modelo={modelo.nombre}  "
              f"de a {concurrencia}  {presupuesto.informe()}\n")

        turno = asyncio.Semaphore(concurrencia)
        sin_tomar_lock = {"n": 0}
        colgados_lock = {"n": 0}

        async def atender(i: int, email) -> None:
            async with turno:
                if presupuesto.agotado():
                    # Sin marca: el correo se queda en la bandeja tal como
                    # llego y la corrida siguiente lo ve como nuevo. Es lo unico
                    # que no puede producir un duplicado.
                    sin_tomar_lock["n"] += 1
                    return
                try:
                    r = await asyncio.wait_for(
                        orq.procesar(email), TOPE_POR_CORREO_S)
                except asyncio.TimeoutError:
                    # El correo conserva su marca de reclamacion, asi que la
                    # corrida siguiente lo retoma con la cuenta puesta y a la
                    # tercera va a una persona. No se le escribe nada aqui: la
                    # tarea quedo cortada a mitad y no se sabe por donde iba.
                    colgados_lock["n"] += 1
                    emitir(
                        f"[{i}/{len(correos)}] "
                        f"[{referencia_del_correo(email.id)}] se abandona: "
                        f"paso de {TOPE_POR_CORREO_S:.0f}s sin terminar, y "
                        f"esperarlo retiene la corrida",
                        severidad=ERROR,
                        solo_json=("correo", "n", "de", "tope_s"),
                        correo=referencia_del_correo(email.id), n=i,
                        de=len(correos), tope_s=TOPE_POR_CORREO_S,
                        asunto=(email.subject or "")[:120],
                    )
                    return
            ref = referencia_del_correo(r.email_id)
            emitir(
                f"[{i}/{len(correos)}] [{ref}] {r.accion} "
                # Un reenvio no tiene numero de radicado ni motivo de revision
                # manual: lo que lo describe es el area a la que fue.
                + (r.motivo_manual or r.radicado or r.area_destino or ""),
                severidad=_severidad_de(r.accion),
                # La primera linea ya los lleva escritos; en el registro siguen
                # para poder filtrar por ellos.
                solo_json=("correo", "n", "de", "accion", "radicado",
                           "motivo_manual", "area_destino"),
                en_su_linea=("asunto", "sin_resolver", "por_que"),
                correo=ref, n=i, de=len(correos), accion=r.accion,
                radicado=r.radicado, motivo_manual=r.motivo_manual,
                area_destino=r.area_destino,
                # La frase del agente habla de un dato que falto, y solo
                # explica el desenlace del correo que se cerro por eso. En un
                # reenvio no hay frase —no se le llama— y lo que explica el
                # desenlace es en que se reconocio el area.
                por_que=(r.razon_del_reenvio or r.razon_del_duplicado or (
                    r.explicacion_del_agente
                    if explica_el_desenlace(r.accion, r.motivo_manual) else "")),
                campos=len(r.hallazgos), tokens_entrada=r.tokens_entrada,
                # Cada vuelta reenvia el prompt entero, asi que es lo que
                # gobierna el consumo. Sin este campo solo se ve el total de la
                # corrida y no se sabe que correo se llevo las vueltas.
                vueltas=r.turnos_del_agente,
                duracion_ms=r.duracion_ms, asunto=r.asunto[:120],
                sin_resolver=sorted(
                    c for c, o in r.atribucion.items()
                    if o is Origen.NO_RESUELTO),
            )

        await asyncio.gather(
            *(atender(i, c) for i, c in enumerate(correos, 1)))

        sin_tomar = sin_tomar_lock["n"]
        colgados = colgados_lock["n"]
        if sin_tomar:
            emitir(
                f"se acabo el tiempo de la corrida: {sin_tomar} correo(s) sin "
                f"tomar, los toma la siguiente",
                severidad=AVISO, sin_tomar=sin_tomar,
                presupuesto=presupuesto.informe(),
            )
    finally:
        await outlook.close()
        await servicio_radicacion.close()
        for servicio in (auditoria,):
            cerrar = getattr(servicio, "close", None)
            if cerrar is None:
                continue
            try:
                resultado = cerrar()
                if asyncio.iscoroutine(resultado):
                    await resultado
            except Exception as e:
                print(f"AVISO: no se cerro {type(servicio).__name__}: {e}")

    # Los correos ya quedaron radicados, marcados y movidos. Si el informe no se
    # puede guardar, eso NO invalida la corrida: se anota y se sigue, porque el
    # informe completo viaja igual por la salida estandar.
    rutas = {}
    fallo_al_guardar = ""
    try:
        rutas = ejecucion.escribir()
    except Exception as e:
        fallo_al_guardar = en_una_linea(f"{type(e).__name__}: {e}")
        emitir(
            f"no se pudo guardar el informe en {LOGS}: {fallo_al_guardar}",
            severidad=ERROR, destino_informe=str(LOGS),
            fallo=fallo_al_guardar,
        )

    resumen = ejecucion.resumen()
    resumen["sin_tomar"] = sin_tomar
    resumen["minutos_usados"] = round(presupuesto.transcurrido() / 60, 1)
    if colgados:
        # Un correo abandonado no tiene desenlace y no aparece en ninguna de
        # las cuentas del informe: sin esta linea, la corrida cuadra mal y no
        # se ve por que.
        resumen["colgados"] = colgados
    if fallo_al_guardar:
        resumen["informe_no_guardado"] = fallo_al_guardar
    # El informe viaja como UN campo. Impreso a pelo, Cloud Logging lo parte en
    # una entrada por renglon y deja de poder leerse como informe.
    emitir(
        f"ejecucion terminada: {resumen['radicados']} radicados, "
        f"{resumen['manual']} a manual de {resumen['correos']} correos",
        severidad=_severidad_del_lote(resumen),
        # El informe que sigue ya trae estos numeros desglosados, y el mensaje
        # lleva los tres que resumen la corrida.
        solo_json=("id_ejecucion", "correos", "radicados", "manual",
                   "duplicados", "reenviados", "aplazados", "fallidos",
                   "llamadas_modelo", "tokens_entrada", "costo_usd",
                   "motivos_manual"),
        informe=ejecucion.informe(),
        archivo_informe=str(rutas.get("ejecucion", "")),
        **resumen,
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
