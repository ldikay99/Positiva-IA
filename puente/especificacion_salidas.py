"""
La ficha de los nodos de «salidas a la medida» (plan, sección 5d): exportar datos
a JSON, XML, CSV, HTML o texto, comprimir archivos en ZIP y llenar una plantilla
de la biblioteca de Documentos (Word, Excel, PowerPoint o correo HTML), también
con la IA. PowerPoint y HTML desde texto o desde la IA van en documento_generar.

`tipos(h)` recibe el módulo `nodos_declarativos` (sus ayudas de esquema) y
devuelve {tipo: ficha}. Lo ejecuta `nodos_salidas.py`; el zod del servidor está
en `servidor/src/flujos_salidas.ts`.
"""
from __future__ import annotations

from typing import Any, Dict

TIPOS = {"archivo_exportar", "archivo_comprimir", "documento_plantilla"}
FORMATOS_EXPORTAR = ("json", "xml", "csv", "html", "txt", "md")
SEPARADORES = (",", ";", "tab")
MODOS_IA = ("llenar", "completo")
FORMATOS_PLANTILLA_SALIDA = ("original", "pdf")


def tipos(h: Any) -> Dict[str, Dict[str, Any]]:
    conf, plantilla, texto, opcion, lista, mapa, objeto = h.conf, h.plantilla, h.texto, h.opcion, h.lista, h.mapa, h.objeto
    SALIDA, ARCHIVOS = h.SALIDA, h.ARCHIVOS
    desde_ia = dict(h.DESDE_IA)
    desde_ia["properties"] = {
        **h.DESDE_IA["properties"],
        "modo": opcion(list(MODOS_IA), "llenar: la IA escribe las variables que el flujo no tiene; completo: además redacta el contenido "
                                       "(secciones, diapositivas u hojas) en {{contenido}} de la plantilla, con su diseño", "llenar"),
        "variables": lista(texto(), "variables que escribe la IA aunque el flujo ya las tenga"),
    }
    desde_ia["description"] = "la IA llena la plantilla (sus variables) o arma el documento completo con el diseño de la plantilla"

    return {
        "archivo_exportar": {
            "nombre": "Exportar a archivo", "categoria": "documentos",
            "descripcion": "Convierte datos del flujo en un archivo: JSON, XML, CSV (con separador «,», «;» o tabulador), HTML (una tabla o un "
                           "documento con estilo), texto o markdown. Con de, toma una variable (una lista de objetos es una tabla; un objeto, "
                           "campos y valores); con contenido, escribe tu propio texto con {{variables}} (en XML y HTML los valores se escapan). "
                           "Queda como archivo del flujo y resultado de la ejecución, para adjuntar o entregar.",
            "config": conf({"formato": opcion(list(FORMATOS_EXPORTAR), "json, xml, csv, html, txt o md"),
                            "de": plantilla("la variable con los datos, p. ej. «filas» o «{{datos}}»"),
                            "contenido": plantilla("o el texto del archivo con {{variables}}", largo=True),
                            "columnas": lista(texto(), "CSV y HTML: qué columnas y en qué orden (admite rutas como «persona.nombre»)"),
                            "separador": opcion(list(SEPARADORES), "CSV: separador de columnas («;» para Excel en español)", ","),
                            "raiz": texto("XML: nombre del elemento raíz", "datos"), "elemento": texto("XML: nombre de cada elemento de una lista", "elemento"),
                            "titulo": plantilla("HTML: título del documento"),
                            "nombre": plantilla("nombre del archivo (sin extensión)", "datos"), "salida": SALIDA()}, ["formato"]),
            "salidas": ["ok"], "produce": "salida {id, nombre, tipo, tamano, sha256, filas}",
            "ejemplo": {"id": "exportar", "tipo": "archivo_exportar", "nombre": "Exportar a CSV", "config": {
                "formato": "csv", "de": "filas", "columnas": ["documento", "nombre", "estado"], "separador": ";", "nombre": "casos-{{hoy}}", "salida": "csv"}},
            "ejemplos": [{"formato": "xml", "de": "facturas", "raiz": "facturas", "elemento": "factura", "nombre": "facturas"},
                         {"formato": "txt", "contenido": "Radicado: {{radicado}}\nEstado: {{estado}}\n", "nombre": "constancia"}],
            "consejos": ["Para Excel con varias pestañas usa hoja_escribir; para Word, PDF o PowerPoint, documento_generar o documento_plantilla.",
                         "En CSV el separador «;» es el que Excel en español abre en columnas; el archivo lleva BOM para que las tildes se vean bien.",
                         "En XML cada campo es un elemento; una lista dentro de un objeto repite el elemento con el nombre del campo. En HTML, una "
                         "lista de objetos sale como tabla con estilo (columnas elige cuáles)."],
        },
        "archivo_comprimir": {
            "nombre": "Comprimir en ZIP", "categoria": "documentos",
            "descripcion": "Junta varios archivos de la ejecución en un .zip (documentos generados, imágenes, archivos subidos o los adjuntos del "
                           "correo): hasta 50 archivos y 30 MB. Los nombres repetidos quedan «nombre (2).ext». Deja la ficha del ZIP para adjuntarlo o entregarlo.",
            "config": conf({"archivos": ARCHIVOS("los archivos a comprimir: «{{documento}}», «{{imagenes}}», «adjuntos», ids «a-…» o filtros {de, nombre, tipo}"),
                            "nombre": plantilla("nombre del ZIP (sin extensión)", "archivos"), "salida": SALIDA()}, ["archivos"]),
            "salidas": ["ok"], "produce": "salida {id, nombre, tipo, tamano, archivos: [nombres], cantidad}",
            "ejemplo": {"id": "comprimir", "tipo": "archivo_comprimir", "nombre": "Juntar en un ZIP", "config": {
                "archivos": ["{{informe}}", "{{hoja}}", "adjuntos"], "nombre": "expediente-{{radicado}}", "salida": "zip"}},
            "ejemplos": [{"archivos": ["{{imagenes}}"], "nombre": "imagenes"}, {"archivos": [{"de": "adjuntos", "tipo": "pdf"}], "nombre": "anexos-pdf"}],
            "consejos": ["Después adjúntalo con «{{zip}}» en correo_enviar, responder_correo o la salida.",
                         "Para unir PDF en uno solo usa pdf_unir; ZIP los deja separados."],
        },
        "documento_plantilla": {
            "nombre": "Llenar plantilla de documento", "categoria": "documentos",
            "descripcion": "Llena una plantilla de la biblioteca de Documentos (Word, Excel, PowerPoint o correo HTML, con el logo, los estilos y el "
                           "diseño de cada área) con las variables del flujo y los datos que le des: {{variable}}, filas o párrafos que se repiten con "
                           "{{#lista}} y {{item.campo}}, imágenes con {{imagen:logo}}. Con desde_ia la IA escribe lo que falta o arma el documento "
                           "completo (secciones, diapositivas u hojas) con el diseño de la plantilla. Queda como archivo de la ejecución; si es HTML, "
                           "deja también el texto en {{salida.html}} (y {{salida.asunto}}) para el cuerpo de un correo.",
            "config": conf({"plantilla": {**texto("el id de una plantilla de la biblioteca (Documentos), p. ej. «carta-respuesta»"), "format": "plantilla-documento",
                                          "title": "Plantilla"},
                            "datos": mapa(plantilla(), "valores para sus variables {variable: plantilla o valor}; lo que no des se toma de las variables del flujo"),
                            "formato": opcion(list(FORMATOS_PLANTILLA_SALIDA), "original (el de la plantilla) o pdf (Word o HTML: conserva texto, títulos y tablas, no el diseño exacto)", "original"),
                            "desde_ia": desde_ia,
                            "nombre": plantilla("nombre del archivo (sin extensión); si no, el de la plantilla"), "salida": SALIDA()}, ["plantilla"]),
            "salidas": ["ok"], "produce": "salida {id, nombre, tipo, tamano, plantilla, formato, faltantes, html?, asunto?}",
            "ejemplo": {"id": "carta", "tipo": "documento_plantilla", "nombre": "Carta de respuesta", "config": {
                "plantilla": "carta-respuesta", "datos": {"nombre": "{{datos.nombre}}", "radicado": "{{radicado}}", "fecha": "{{hoy}}"},
                "nombre": "respuesta-{{radicado}}", "salida": "carta"}},
            "ejemplos": [{"plantilla": "correo-bienvenida", "datos": {"nombre": "{{entrada.nombre}}"}, "salida": "correo_html"},
                         {"plantilla": "presentacion-comite", "salida": "presentacion", "desde_ia": {
                             "modo": "completo", "instrucciones": "Arma 3 diapositivas con los resultados del mes: logros, cifras y próximos pasos.",
                             "entradas": ["resumen"]}}],
            "consejos": ["Las plantillas se suben o se crean en Documentos (o con Vera): ahí ves sus variables y sus listas. Una fila de tabla (o un "
                         "párrafo) con {{#items}} se repite por cada elemento, con {{item.campo}} e {{indice}} (en HTML, {{#items}} … {{/items}}); "
                         "{{imagen:logo}} pone el logo guardado con la plantilla o una imagen de la ejecución ({{imagen:imagenes.0}}).",
                         "Con desde_ia modo llenar la IA escribe solo las variables que el flujo no tiene; modo completo además redacta el contenido en "
                         "{{contenido}} (o al final) con el diseño de la plantilla. {{salida.faltantes}} dice qué quedó vacío. Las variables de "
                         "entorno nunca se leen desde una plantilla.",
                         "Para un correo HTML: correo_enviar o responder_correo con html «{{correo_html.html}}» y asunto «{{correo_html.asunto}}»."],
        },
    }
