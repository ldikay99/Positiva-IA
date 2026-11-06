"""
Registro de operaciones del agente y motivos funcionales de no radicacion.

Cada paso del procesamiento de un hilo se imprime en consola con un formato
uniforme para poder seguir en los logs que hizo el agente y que no pudo hacer.

Los motivos describen el caso en terminos del negocio (que falto en el correo,
que no se pudo identificar). No exponen detalles de infraestructura: codigos de
respuesta, tiempos de espera, permisos o nombres de servicios.

Autor: Jonathan Paredes (ldikay99)
       jonathan.paredes@linktic.com
"""

# Motivo funcional por cada etiqueta de error que usa el orquestador.
# El texto se imprime en consola y se envia al buzon interno.
MOTIVOS = {
    "DOMINIO-DE-CORREO-INVALIDO":
        "El correo no proviene de un despacho de la Rama Judicial, "
        "unico origen autorizado para radicacion automatica.",
    "NO-ES-TUTELA":
        "El contenido del correo no corresponde a una accion de tutela "
        "ni a una actuacion de un proceso de tutela.",
    "CORREO-CON-ENLACE":
        "El expediente se remite mediante un enlace externo, por lo que la "
        "documentacion completa debe consultarse y radicarse manualmente.",
    "SIN-RADICADO-ENCONTRADO":
        "No se identifico el numero de radicado de 23 digitos, ni en el texto "
        "del correo ni en los documentos anexos.",
    "SIN-DOCUMENTACION-ADJUNTA":
        "El correo no trae la documentacion del despacho en formato PDF, "
        "por lo que no hay actuacion que radicar.",
    "JUZGADO-NO-ENCONTRADO":
        "El juzgado correspondiente al radicado no figura en el directorio de "
        "despachos, por lo que no es posible completar los datos del tramite.",
    "RADICADO-DUPLICADO":
        "La documentacion de este correo ya fue radicada anteriormente, "
        "por lo que no se genero un nuevo tramite.",
    "RADICACION-POR-CONFIRMAR":
        "La radicacion se envio pero no se obtuvo confirmacion, por lo que el "
        "tramite pudo haber quedado creado. Antes de radicar manualmente se debe "
        "verificar en el sistema si el registro ya existe, para no duplicarlo.",
    "ERROR-RADICAR":
        "El sistema no acepto el registro de la tutela y el tramite no quedo creado.",
    "ERROR-INESPERADO":
        "El correo no pudo procesarse de forma automatica.",
}

MOTIVO_POR_DEFECTO = "El caso requiere revision manual."

# Cierres del correo segun lo que debe hacer el area. El caso por confirmar no
# puede invitar a radicar de una vez, porque el tramite pudo quedar creado.
ACCION_SUGERIDA = {
    "RADICACION-POR-CONFIRMAR":
        "El caso queda pendiente de verificacion.",
}

ACCION_POR_DEFECTO = "El caso queda disponible para gestion manual."


def motivo(etiqueta: str) -> str:
    """Descripcion funcional de por que no se radico un hilo."""
    return MOTIVOS.get(etiqueta, MOTIVO_POR_DEFECTO)


def accion(etiqueta: str) -> str:
    """Que debe hacer el area con el caso."""
    return ACCION_SUGERIDA.get(etiqueta, ACCION_POR_DEFECTO)


def paso(numero, nombre: str, detalle: str = ""):
    """Inicio de una operacion del flujo."""
    marca = f"[PASO {numero}]" if numero else "[PASO]"
    print(f"   {marca} {nombre}" + (f": {detalle}" if detalle else ""), flush=True)


def ok(nombre: str, detalle: str = ""):
    """Operacion completada."""
    print(f"   [OK] {nombre}" + (f": {detalle}" if detalle else ""), flush=True)


def omitido(nombre: str, detalle: str = ""):
    """Operacion que no se ejecuta por una condicion esperada del caso."""
    print(f"   [OMITIDO] {nombre}" + (f": {detalle}" if detalle else ""), flush=True)


def fallo(nombre: str, detalle: str = ""):
    """Operacion que no se pudo completar."""
    print(f"   [FALLO] {nombre}" + (f": {detalle}" if detalle else ""), flush=True)


def no_radicado(etiqueta: str):
    """Cierre del hilo sin radicacion, con el motivo funcional."""
    print(f"   [NO RADICADO] {etiqueta}: {motivo(etiqueta)}", flush=True)


def radicado(numero_radicado: str, radicado_23: str, n_archivos: int):
    """Cierre del hilo con radicacion exitosa."""
    print(f"   [RADICADO] {numero_radicado} | proceso {radicado_23} | "
          f"{n_archivos} archivo(s)", flush=True)
