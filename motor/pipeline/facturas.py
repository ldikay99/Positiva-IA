"""
Facturas electronicas que llegan al buzon de PQRD.

Una factura no es la peticion de nadie: tiene su propio tramite y radicarla como
PQRD la mete en una cola que no le corresponde y le fija un termino de respuesta
que no tiene.

Lo que la distingue es la FORMA del asunto con que la emite un proveedor de
facturacion electronica —`NIT;EMPRESA;NUMERO;TIPO`— o las marcas que solo lleva
un documento de la DIAN. No basta que el correo venga de un buzon que se llame
"facturacion": una empresa escribe desde ese buzon para pedir un tramite propio,
y eso si es una PQRD.
"""
from __future__ import annotations

import re

MOTIVO = "FACTURA_ELECTRONICA"

# El asunto con que los proveedores emiten la factura: NIT, razon social,
# numero del documento y codigo del tipo. El ultimo separador es opcional
# porque no todos los proveedores lo ponen, y exigirlo dejaba pasar la factura
# cuyo asunto termina en el codigo.
#
# El numero del documento mezcla letras y digitos en cualquier orden: una
# factura de venta sale como `FEV1234` y una nota credito como `NCR12F45`.
# Exigir que las letras fueran solo un prefijo dejaba pasar un tercio de las
# notas credito de un emisor real.
#
# El segundo segmento tiene que llevar letras: es una razon social. Sin esa
# exigencia, cuatro numeros separados por punto y coma —una cedula y un
# telefono escritos asi— pasaban por factura.
_ASUNTO_DE_FACTURA = re.compile(
    r"^\s*\d{6,}\s*;(?=[^;]*[A-Za-zÁÉÍÓÚÑáéíóúñ])[^;]{2,}?;"
    r"\s*(?=[A-Z0-9]*\d)[A-Z0-9]{2,}\s*;\s*\d{1,3}\b",
    re.IGNORECASE,
)

# El identificador unico que la DIAN le pone a cada documento electronico. No
# aparece en ningun correo que no sea uno.
_CODIGO_DE_LA_DIAN = re.compile(r"\bcu[fd]e\s*[:=]", re.IGNORECASE)

# Como se nombra a si mismo un documento electronico. Se exige la forma
# completa: "factura" a secas sale en PQRD legitimas que reclaman un cobro.
_DOCUMENTO_DE_LA_DIAN = re.compile(
    r"factura\s+electr[oó]nica\s+de\s+venta"
    r"|nota\s+(?:cr[eé]dito|d[eé]bito)\s+electr[oó]nica"
    r"|representaci[oó]n\s+gr[aá]fica\s+de\s+la\s+factura",
    re.IGNORECASE,
)

# Cuanto del cuerpo se mira. El encabezado de un documento electronico lo
# declara de entrada; mas abajo solo hay tablas y avisos legales.
TOPE_DEL_CUERPO = 4000


def es_factura(asunto: str = "", cuerpo: str = "") -> str:
    """
    Por que este correo es una factura electronica, o cadena vacia.

    Devuelve la evidencia para que quien lo revise no tenga que adivinar en que
    se reconocio.
    """
    m = _ASUNTO_DE_FACTURA.match(asunto or "")
    if m:
        return (f"el asunto tiene la forma con que se emite una factura "
                f"electronica: \"{m.group(0).strip()[:60]}\"")

    principio = (cuerpo or "")[:TOPE_DEL_CUERPO]
    if _CODIGO_DE_LA_DIAN.search(principio):
        return "el cuerpo trae el codigo unico de un documento electronico"

    m = _DOCUMENTO_DE_LA_DIAN.search(principio)
    if m:
        return f"el cuerpo se declara documento electronico: \"{m.group(0)}\""
    return ""
