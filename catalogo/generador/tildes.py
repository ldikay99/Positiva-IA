"""
Tildes en los textos que se leen del catálogo.

Las descripciones salen de comentarios y docstrings del código del motor, que
se escribieron sin tildes. Aquí se corrigen SOLO los campos de texto para leer
(descripción, nombre, notas): nunca claves, valores, regex ni rutas, que deben
quedar idénticos al código. Solo palabras sin ambigüedad («esta», «mas», «aun»
o «solo» pueden ir con o sin tilde según el sentido, y se dejan como están).
"""
import re
from typing import Any

CAMPOS = {"descripcion", "nombre", "nota_sobrescritura", "titulo", "ayuda", "detalle", "description"}

PALABRAS = {
    "codigo": "código", "codigos": "códigos", "numero": "número", "numeros": "números",
    "validacion": "validación", "validaciones": "validaciones", "radicacion": "radicación",
    "informacion": "información", "direccion": "dirección", "clasificacion": "clasificación",
    "extraccion": "extracción", "deteccion": "detección", "correccion": "corrección",
    "tambien": "también", "segun": "según", "unico": "único", "unica": "única",
    "razon": "razón", "politica": "política", "telefono": "teléfono", "telefonos": "teléfonos",
    "identificacion": "identificación", "configuracion": "configuración", "funcion": "función",
    "seccion": "sección", "pagina": "página", "paginas": "páginas", "ultimo": "último", "ultima": "última",
    "ultimos": "últimos", "ultimas": "últimas", "electronico": "electrónico", "electronica": "electrónica",
    "automatico": "automático", "automatica": "automática", "automaticamente": "automáticamente",
    "busqueda": "búsqueda", "busquedas": "búsquedas", "minimo": "mínimo", "maximo": "máximo",
    "despues": "después", "ademas": "además", "asi": "así", "aqui": "aquí", "ahi": "ahí",
    "podria": "podría", "deberia": "debería", "seria": "sería", "habia": "había",
    "metodo": "método", "parametro": "parámetro",
    "parametros": "parámetros", "cedula": "cédula", "cedulas": "cédulas", "nit": "NIT",
    "dias": "días", "dia": "día", "regimen": "régimen", "juridica": "jurídica", "juridico": "jurídico",
    "peticion": "petición", "peticiones": "peticiones", "notificacion": "notificación",
    "decision": "decisión", "revision": "revisión",
    "region": "región", "municipio": "municipio", "medico": "médico", "medica": "médica",
    "medicos": "médicos", "diagnostico": "diagnóstico", "analisis": "análisis", "genero": "género",
    "imagenes": "imágenes", "imagen": "imagen", "vision": "visión", "camara": "cámara",
    "linea": "línea", "lineas": "líneas", "tecnico": "técnico", "tecnica": "técnica",
    "practica": "práctica", "basico": "básico", "basica": "básica", "rapido": "rápido",
    "rapida": "rápida", "valido": "válido", "valida": "válida", "invalido": "inválido",
    "invalida": "inválida", "proposito": "propósito", "logica": "lógica", "heuristica": "heurística",
    "heuristicas": "heurísticas", "patron": "patrón", "caracteres": "caracteres", "caracter": "carácter",
    "comun": "común", "comunes": "comunes", "tambien,": "también,", "despues,": "después,",
    "envio": "envío", "envia": "envía", "reenvia": "reenvía", "reenvio": "reenvío",
    "dueno": "dueño", "senal": "señal", "senales": "señales", "ano": "año", "anos": "años",
    "minusculas": "minúsculas", "mayusculas": "mayúsculas", "detras": "detrás", "util": "útil", "utiles": "útiles",
    "modulo": "módulo", "modulos": "módulos", "facil": "fácil", "dificil": "difícil", "condicion": "condición",
    "opcion": "opción", "version": "versión", "conexion": "conexión", "conversion": "conversión",
    "extension": "extensión", "sesion": "sesión", "transcripcion": "transcripción", "intencion": "intención",
    "compania": "compañía", "companias": "compañías", "espanol": "español",
}
# Una palabra pegada a un punto, guion bajo o paréntesis es un nombre de código (r.accion, _seccion()): no se toca.
_RE = re.compile(r"(?<![\w.])(" + "|".join(sorted((re.escape(k) for k in PALABRAS), key=len, reverse=True)) + r")(?!\w|\.\w|\()", re.IGNORECASE)


def _con_tildes(texto: str) -> str:
    def cambiar(m: "re.Match[str]") -> str:
        w = m.group(0)
        nuevo = PALABRAS[w.lower()]
        if w.isupper() and len(w) > 1:
            return nuevo.upper()
        if w[0].isupper():
            return nuevo[0].upper() + nuevo[1:]
        return nuevo
    # Nada que parezca código: lo que va entre `comillas invertidas` queda igual.
    partes = re.split(r"(`[^`]*`)", texto)
    return "".join(p if p.startswith("`") else _RE.sub(cambiar, p) for p in partes)


def corregir(x: Any, clave: str = "") -> Any:
    if isinstance(x, dict):
        return {k: corregir(v, k) for k, v in x.items()}
    if isinstance(x, list):
        return [corregir(v, clave) for v in x]
    if isinstance(x, str) and clave in CAMPOS:
        return _con_tildes(x)
    return x
