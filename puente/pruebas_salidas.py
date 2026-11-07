"""
Pruebas de «salidas a la medida» (plan, sección 5d): PowerPoint, HTML, XML, JSON,
CSV y ZIP como archivos de la ejecución, la biblioteca de plantillas de
documentos (Word, Excel, PowerPoint y correo HTML con variables, filas que se
repiten, logos y {{contenido}}) y la IA que las llena o arma el documento
completo. Sin red: la IA es el modelo falso de pruebas_declarativo.

    PYTHONPATH=motor python puente/pruebas_salidas.py        (desde la raíz; en el contenedor: /app)
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
os.environ["CACHE_CONVERSION"] = "no"

import flujo_declarativo as fd  # noqa: E402
import nodos_declarativos as nd  # noqa: E402
import nodos_salidas as ns  # noqa: E402
import salidas as m_sal  # noqa: E402
from pruebas_declarativo import Producto, cadena, nodo, png  # noqa: E402


def docx_bytes(construir) -> bytes:
    import docx
    d = docx.Document()
    construir(d)
    s = io.BytesIO()
    d.save(s)
    return s.getvalue()


class _BaseSalidas(Producto):
    def plantilla(self, pid, formato, datos, **meta):
        """Una plantilla en la biblioteca, como la deja el servidor (<estado>/documentos/<id>/)."""
        carpeta = self.estado / "documentos" / pid
        (carpeta / "recursos").mkdir(parents=True, exist_ok=True)
        (carpeta / f"plantilla.{formato}").write_bytes(datos)
        (carpeta / "meta.json").write_text(json.dumps({"id": pid, "nombre": meta.pop("nombre", pid), "formato": formato, "archivo": f"plantilla.{formato}",
                                                        "recursos": [], **meta}), encoding="utf-8")
        return carpeta

    def leer(self, ficha) -> bytes:
        return self.ruta_de(ficha).read_bytes()

    def manual_con(self, *nodos, campos=None, **entrada):
        return self.correr_orden(self.con(*nodos, campos=campos or [{"nombre": k, "tipo": "lista" if isinstance(v, list) else "objeto" if isinstance(v, dict) else "texto"}
                                                                    for k, v in entrada.items()]), entrada=entrada)


FILAS = [{"documento": "111", "nombre": "Ana Pérez", "monto": 1500}, {"documento": "222", "nombre": "Luis <b>", "monto": 250.5}]


class FormatosDeSalida(_BaseSalidas):
    """documento_generar en PowerPoint y HTML; archivo_exportar a JSON, XML, CSV, HTML, texto y markdown; ZIP."""

    def test_powerpoint_desde_texto_y_desde_la_ia(self):
        from pptx import Presentation
        r, ev = self.manual_con(nodo("pres", "documento_generar", formato="pptx", nombre="informe", salida="pres",
                                     plantilla="# Informe de {{entrada.mes}}\n\nÁrea de servicio\n\n## Resumen\n- Casos: {{entrada.total}}\n- Sin pendientes\n\n## Cifras\n| Mes | Casos |\n| {{entrada.mes}} | {{entrada.total}} |"),
                                mes="octubre", total="42")
        f = r["variables"]["pres"]
        self.assertEqual((f["nombre"], f["tipo"]), ("informe.pptx", m_sal.MIME["pptx"]))
        prs = Presentation(io.BytesIO(self.leer(f)))
        self.assertEqual(len(prs.slides), 3)  # portada + 2
        texto = m_sal.texto_pptx(self.leer(f))
        self.assertIn("Informe de octubre", texto)
        self.assertIn("Casos: 42", texto)
        self.assertIn("| octubre | 42 |", texto)
        self.assertTrue(any(a["nombre"] == "informe.pptx" for a in r["archivos"]))  # resultado de la ejecución (historial, chat, webhook)
        # la IA arma título, diapositivas con puntos, tabla y notas
        self.modelo.r["json"] = {"titulo": "Resultados del trimestre", "subtitulo": "Comité", "diapositivas": [
            {"titulo": "Contexto", "puntos": ["Más casos", "Menos tiempo"], "notas": "Empezar por el contexto"},
            {"titulo": "Cifras", "tabla": {"columnas": ["Mes", "Casos"], "filas": [["Julio", "10"], ["Agosto", "12"]]}},
            {"titulo": "Próximos pasos", "puntos": ["Automatizar la respuesta"]}]}
        r, _ = self.manual_con(nodo("pres", "documento_generar", formato="pptx", salida="pres",
                                    desde_ia={"instrucciones": "3 diapositivas sobre {{entrada.tema}}", "entradas": ["entrada"]}), tema="el trimestre")
        f = r["variables"]["pres"]
        self.assertEqual((f["nombre"], f["diapositivas"]), ("Resultados del trimestre.pptx", 4))
        prs = Presentation(io.BytesIO(self.leer(f)))
        self.assertEqual(prs.slides[1].notes_slide.notes_text_frame.text, "Empezar por el contexto")
        self.assertEqual(prs.slide_width, 12191695)  # 16:9
        llamada = self.modelo.llamadas[-1]
        self.assertIn("diapositivas", llamada["esquema"]["properties"])
        self.assertIn("3 diapositivas sobre el trimestre", llamada["sistema"])
        # sin diapositivas: un motivo claro
        self.modelo.r["json"] = {"titulo": "x"}
        r, _ = self.correr_orden(self.con(nodo("pres", "documento_generar", formato="pptx", desde_ia={"instrucciones": "x"})), exigir_ok=False, entrada={})
        self.assertIn("no devolvió una presentación", r["motivo"])

    def test_html_y_plantilla_pptx_del_flujo(self):
        from pptx import Presentation
        r, _ = self.manual_con(nodo("h", "documento_generar", formato="html", nombre="aviso", salida="h", plantilla="# Hola {{entrada.nombre}}\n\n- uno\n- dos"),
                               nombre="Ana <script>")
        html = self.leer(r["variables"]["h"]).decode("utf-8")
        self.assertIn("<h1>Hola Ana &lt;script&gt;</h1>", html)
        self.assertIn("<ul><li>uno</li><li>dos</li></ul>", html)
        self.assertEqual(r["variables"]["h"]["tipo"], "text/html")
        # un .pptx subido al flujo con {{variables}}
        prs = Presentation()
        s = prs.slides.add_slide(prs.slide_layouts[1])
        s.shapes.title.text = "Propuesta para {{entrada.cliente}}"
        s.placeholders[1].text_frame.text = "{{#entrada.puntos}}{{item}}"
        b = io.BytesIO()
        prs.save(b)
        aid = self.archivo("propuesta.pptx", b.getvalue())
        r, _ = self.manual_con(nodo("p", "documento_generar", formato="pptx", plantilla_archivo=aid, nombre="propuesta-{{entrada.cliente}}", salida="p"),
                               cliente="ACME", puntos=["Ahorro", "Rapidez"])
        texto = m_sal.texto_pptx(self.leer(r["variables"]["p"]))
        self.assertIn("Propuesta para ACME", texto)
        self.assertIn("Ahorro\nRapidez", texto)
        # un .pptx con formato docx no se puede
        r, _ = self.correr_orden(self.con(nodo("p", "documento_generar", formato="docx", plantilla_archivo=aid)), exigir_ok=False, entrada={})
        self.assertIn("formato tiene que ser pptx", r["motivo"])

    def test_exportar_a_cada_formato(self):
        conf = {"json": {}, "xml": {"raiz": "casos", "elemento": "caso"}, "csv": {"separador": ";", "columnas": ["documento", "monto"]},
                "html": {"titulo": "Casos"}, "txt": {}, "md": {}}
        nodos = [nodo(f"x_{k}", "archivo_exportar", formato=k, de="{{entrada.filas}}", nombre=f"casos-{k}", salida=f"x_{k}", **c) for k, c in conf.items()]
        r, _ = self.manual_con(*nodos, filas=FILAS)
        v = r["variables"]
        self.assertEqual(json.loads(self.leer(v["x_json"])), FILAS)
        xml = self.leer(v["x_xml"]).decode("utf-8")
        self.assertIn("<casos>", xml)
        self.assertEqual(xml.count("<caso>"), 2)
        self.assertIn("<nombre>Luis &lt;b&gt;</nombre>", xml)
        csv_ = self.leer(v["x_csv"]).decode("utf-8-sig")
        self.assertEqual(csv_.splitlines(), ["documento;monto", "111;1500", "222;250.5"])
        html = self.leer(v["x_html"]).decode("utf-8")
        self.assertIn("<th>documento</th>", html)
        self.assertIn("<td>Luis &lt;b&gt;</td>", html)
        self.assertIn("| documento | nombre | monto |", self.leer(v["x_md"]).decode("utf-8"))
        self.assertEqual((v["x_csv"]["filas"], v["x_csv"]["tipo"]), (2, "text/csv"))
        self.assertEqual(len([a for a in r["archivos"] if a["nombre"].startswith("casos-")]), 6)
        # con contenido: el texto propio, con los valores escapados en XML y JSON
        r, _ = self.manual_con(nodo("x", "archivo_exportar", formato="xml", contenido="<a nombre=\"{{entrada.n}}\">{{entrada.n}}</a>", salida="x"),
                               nodo("j", "archivo_exportar", formato="json", contenido="{\"n\": \"{{entrada.n}}\"}", salida="j"), n="dice \"hola\" <y>")
        self.assertEqual(self.leer(r["variables"]["x"]).decode("utf-8"), "<a nombre=\"dice &quot;hola&quot; &lt;y&gt;\">dice &quot;hola&quot; &lt;y&gt;</a>")
        self.assertEqual(json.loads(self.leer(r["variables"]["j"])), {"n": "dice \"hola\" <y>"})

    def test_comprimir_en_zip(self):
        r, _ = self.manual_con(nodo("a", "archivo_exportar", formato="txt", contenido="uno", nombre="nota", salida="a"),
                               nodo("b", "archivo_exportar", formato="txt", contenido="dos", nombre="nota", salida="b"),
                               nodo("z", "archivo_comprimir", archivos=["{{a}}", "{{b}}"], nombre="paquete-{{entrada.n}}", salida="zip"), n="7")
        f = r["variables"]["zip"]
        self.assertEqual((f["nombre"], f["cantidad"], f["archivos"]), ("paquete-7.zip", 2, ["nota.txt", "nota (2).txt"]))
        with zipfile.ZipFile(io.BytesIO(self.leer(f))) as z:
            self.assertEqual([z.read(n) for n in z.namelist()], [b"uno", b"dos"])  # con contenido, el texto tal cual
        r, _ = self.correr_orden(self.con(nodo("z", "archivo_comprimir", archivos=[{"de": "archivos", "tipo": "pdf"}])), exigir_ok=False, entrada={})
        self.assertIn("no trajeron ningún archivo", r["motivo"])

    def test_texto_para_el_visor(self):
        import texto_de_archivo as t
        ruta = self.estado / "x.pptx"
        ruta.write_bytes(m_sal.pptx_de_presentacion({"titulo": "Visor", "diapositivas": [{"titulo": "Uno", "puntos": ["a"]}]}))
        self.assertIn("## Diapositiva 2\nUno\na", t.texto(str(ruta), "x.pptx"))
        z, _ = m_sal.zip_de([("a.txt", b"1")])
        (self.estado / "x.zip").write_bytes(z)
        self.assertIn("a.txt", t.texto(str(self.estado / "x.zip"), "x.zip"))


class BibliotecaDeDocumentos(_BaseSalidas):
    """documento_plantilla: Word, Excel, PowerPoint y correo HTML de la biblioteca, con filas, imágenes, entorno vetado e IA."""

    def word(self):
        def construir(d):
            d.add_paragraph("Señor(a) {{nombre}}, radicado {{radicado}}. Clave: {{entorno.SECRETO}}")
            d.add_paragraph("{{imagen:logo 3cm}}")
            d.add_paragraph("- {{item}} {{#pasos}}")
            t = d.add_table(rows=2, cols=2)
            t.rows[0].cells[0].text, t.rows[0].cells[1].text = "Concepto", "Valor"
            t.rows[1].cells[0].text, t.rows[1].cells[1].text = "{{#items}}{{item.concepto}}", "{{item.valor}}"
            d.add_paragraph("{{contenido}}")
            d.sections[0].header.paragraphs[0].text = "Área de {{area}}"
        return docx_bytes(construir)

    def test_word_con_filas_imagen_encabezado_y_entorno_vetado(self):
        import docx
        carpeta = self.plantilla("carta-respuesta", "docx", self.word(), nombre="Carta de respuesta")
        (carpeta / "recursos" / "logo.png").write_bytes(png(40, 20))
        r, ev = self.correr_orden(self.con(nodo("carta", "documento_plantilla", plantilla="carta-respuesta", salida="carta", nombre="carta-{{entrada.radicado}}",
                                                datos={"nombre": "{{entrada.nombre}}", "radicado": "{{entrada.radicado}}", "area": "servicio",
                                                       "items": "{{entrada.items}}", "pasos": "{{entrada.pasos}}"}),
                                           campos=[{"nombre": "nombre"}, {"nombre": "radicado"}, {"nombre": "items", "tipo": "lista"}, {"nombre": "pasos", "tipo": "lista"}]),
                                  entrada={"nombre": "Ana {{entorno.SECRETO}}", "radicado": "R-9", "items": [{"concepto": "Consulta", "valor": 1000}, {"concepto": "Examen", "valor": 2500}],
                                           "pasos": ["Revisar", "Responder"]}, entorno={"SECRETO": "no-debe-salir"})
        f = r["variables"]["carta"]
        self.assertEqual((f["nombre"], f["plantilla"], f["formato"]), ("carta-R-9.docx", "carta-respuesta", "docx"))
        d = docx.Document(io.BytesIO(self.leer(f)))
        textos = [p.text for p in d.paragraphs]
        # el valor con «{{…}}» queda tal cual (no se vuelve a leer como plantilla) y el entorno no se lee desde la plantilla
        self.assertEqual(textos[0], "Señor(a) Ana {{entorno.SECRETO}}, radicado R-9. Clave: ")
        self.assertNotIn("no-debe-salir", json.dumps(textos) + self.salida.getvalue())
        self.assertEqual(textos[2:4], ["- Revisar ", "- Responder "])
        self.assertEqual([[c.text for c in fila.cells] for fila in d.tables[0].rows], [["Concepto", "Valor"], ["Consulta", "1000"], ["Examen", "2500"]])
        self.assertEqual(d.sections[0].header.paragraphs[0].text, "Área de servicio")
        self.assertEqual(len(d.inline_shapes), 1)  # el logo
        self.assertTrue(any("variables de entorno" in a for a in r["avisos"]))
        self.assertNotIn("entorno.SECRETO", f["faltantes"])
        # a PDF (texto, títulos y tablas)
        r, _ = self.correr_orden(self.con(nodo("carta", "documento_plantilla", plantilla="carta-respuesta", formato="pdf", datos={"nombre": "Ana"}, salida="carta")), entrada={})
        f = r["variables"]["carta"]
        self.assertEqual((f["formato"], self.leer(f)[:5]), ("pdf", b"%PDF-"))
        self.assertIn("radicado", f["faltantes"])

    def test_excel_conserva_numeros_estilos_y_repite_filas(self):
        import openpyxl
        from openpyxl.styles import Font
        libro = openpyxl.Workbook()
        ws = libro.active
        ws.title = "Informe"
        ws["A1"] = "Informe de {{mes}}"
        ws["A1"].font = Font(bold=True, size=16)
        ws["A2"], ws["B2"] = "Concepto", "Valor"
        ws["A3"], ws["B3"] = "{{#items}}{{item.concepto}}", "{{item.valor}}"
        ws["A4"], ws["B4"] = "Total", "=SUM(B3:B3)"
        b = io.BytesIO()
        libro.save(b)
        self.plantilla("informe-mensual", "xlsx", b.getvalue())
        r, _ = self.manual_con(nodo("x", "documento_plantilla", plantilla="informe-mensual", datos={"mes": "{{entrada.mes}}", "items": "{{entrada.items}}"}, salida="x"),
                               mes="octubre", items=[{"concepto": "A", "valor": 10}, {"concepto": "B", "valor": 2.5}, {"concepto": "C", "valor": "7"}])
        ws = openpyxl.load_workbook(io.BytesIO(self.leer(r["variables"]["x"])))["Informe"]
        self.assertEqual(ws["A1"].value, "Informe de octubre")
        self.assertTrue(ws["A1"].font.bold)
        self.assertEqual([[c.value for c in fila] for fila in ws.iter_rows(min_row=3, max_row=6)], [["A", 10], ["B", 2.5], ["C", 7], ["Total", "=SUM(B3:B3)"]])

    def test_powerpoint_y_correo_html(self):
        from pptx import Presentation
        from pptx.util import Inches
        prs = Presentation()
        s = prs.slides.add_slide(prs.slide_layouts[1])
        s.shapes.title.text = "Comité de {{mes}}"
        s.placeholders[1].text_frame.text = "{{#logros}}{{item}}"
        caja = s.shapes.add_textbox(Inches(7), Inches(0.2), Inches(2), Inches(1))
        caja.text_frame.text = "{{imagen:logo}}"
        tabla = s.shapes.add_table(2, 2, Inches(1), Inches(5), Inches(6), Inches(1)).table
        tabla.cell(0, 0).text, tabla.cell(0, 1).text = "Indicador", "Valor"
        tabla.cell(1, 0).text, tabla.cell(1, 1).text = "{{#cifras}}{{item.nombre}}", "{{item.valor}}"
        b = io.BytesIO()
        prs.save(b)
        carpeta = self.plantilla("comite", "pptx", b.getvalue())
        (carpeta / "recursos" / "logo.png").write_bytes(png(30, 30))
        r, _ = self.manual_con(nodo("p", "documento_plantilla", plantilla="comite", salida="p",
                                    datos={"mes": "{{entrada.mes}}", "logros": "{{entrada.logros}}", "cifras": "{{entrada.cifras}}"}),
                               mes="noviembre", logros=["Menos tiempo", "Más casos"], cifras=[{"nombre": "Casos", "valor": "42"}])
        datos = self.leer(r["variables"]["p"])
        texto = m_sal.texto_pptx(datos)
        self.assertIn("Comité de noviembre\nMenos tiempo\nMás casos", texto)
        self.assertIn("| Casos | 42 |", texto)
        self.assertNotIn("imagen:logo", texto)
        self.assertEqual(sum(1 for f in Presentation(io.BytesIO(datos)).slides[0].shapes if f.shape_type == 13), 1)  # la imagen
        # el correo HTML: valores escapados, {{{x}}} sin escapar, secciones que se repiten, logo incrustado, html y asunto en la salida
        html = ("<html><body><img src=\"{{imagen:logo}}\"><h1>Hola {{nombre}}</h1><div>{{{firma}}}</div>"
                "<ul>{{#pasos}}<li>{{indice}}. {{item}}</li>{{/pasos}}</ul></body></html>")
        carpeta = self.plantilla("correo-bienvenida", "html", html.encode("utf-8"), asunto="Bienvenida, {{nombre}}")
        (carpeta / "recursos" / "logo.png").write_bytes(png(10, 10))
        r, _ = self.manual_con(nodo("c", "documento_plantilla", plantilla="correo-bienvenida", salida="correo_html",
                                    datos={"nombre": "{{entrada.nombre}}", "firma": "<b>Equipo</b>", "pasos": "{{entrada.pasos}}"}),
                               nombre="Ana & Luis", pasos=["Entrar", "Revisar"])
        c = r["variables"]["correo_html"]
        self.assertIn("<h1>Hola Ana &amp; Luis</h1><div><b>Equipo</b></div><ul><li>1. Entrar</li><li>2. Revisar</li></ul>", c["html"])
        self.assertIn("src=\"data:image/png;base64,", c["html"])
        self.assertEqual((c["asunto"], c["tipo"], c["nombre"]), ("Bienvenida, Ana & Luis", "text/html", "correo-bienvenida.html"))

    def test_la_ia_llena_lo_que_falta_y_arma_el_documento_completo(self):
        import docx
        self.plantilla("carta-respuesta", "docx", self.word())
        self.modelo.r["json"] = {"valores": {"radicado": "IA-1", "items": [{"concepto": "Escrito por la IA", "valor": "1"}], "pasos": ["Paso de la IA"]},
                                 "contenido": {"titulo": "Respuesta", "secciones": [{"titulo": "Decisión", "parrafos": ["Se aprueba la solicitud."], "lista": ["Paso 1"]}]}}
        r, _ = self.manual_con(nodo("carta", "documento_plantilla", plantilla="carta-respuesta", datos={"nombre": "{{entrada.nombre}}", "area": "jurídica"}, salida="carta",
                                    desde_ia={"modo": "completo", "instrucciones": "Responde a {{entrada.nombre}}", "entradas": ["entrada"]}), nombre="Ana")
        d = docx.Document(io.BytesIO(self.leer(r["variables"]["carta"])))
        textos = [p.text for p in d.paragraphs]
        self.assertTrue(textos[0].startswith("Señor(a) Ana, radicado IA-1."))
        self.assertIn("Decisión", textos)
        self.assertIn("Se aprueba la solicitud.", textos)
        self.assertNotIn("{{contenido}}", "\n".join(textos))
        self.assertEqual(d.tables[0].rows[1].cells[0].text, "Escrito por la IA")
        esquema = self.modelo.llamadas[-1]["esquema"]["properties"]
        # la IA solo escribe lo que el flujo no tiene (nombre y área vienen en datos) y, en modo completo, el contenido
        self.assertEqual(set(esquema["valores"]["properties"]), {"radicado", "items", "pasos"})
        self.assertEqual(esquema["valores"]["properties"]["pasos"]["items"], {"type": "string"})
        self.assertIn("secciones", esquema["contenido"]["properties"])
        self.assertIn("Responde a Ana", self.modelo.llamadas[-1]["sistema"])
        self.assertEqual(r["variables"]["carta"]["faltantes"], [])
        # PowerPoint completo: diapositivas nuevas con el diseño de la plantilla, en el lugar de {{contenido}}
        from pptx import Presentation
        prs = Presentation()
        prs.slides.add_slide(prs.slide_layouts[0]).shapes.title.text = "Portada {{mes}}"
        prs.slides.add_slide(prs.slide_layouts[1]).shapes.title.text = "{{contenido}}"
        prs.slides.add_slide(prs.slide_layouts[1]).shapes.title.text = "Gracias"
        b = io.BytesIO()
        prs.save(b)
        self.plantilla("presentacion-comite", "pptx", b.getvalue())
        self.modelo.r["json"] = {"valores": {"mes": "diciembre"}, "contenido": {"titulo": "x", "diapositivas": [
            {"titulo": "Logros", "puntos": ["Uno", "Dos"]}, {"titulo": "Cifras", "puntos": ["Tres"], "notas": "nota"}]}}
        r, _ = self.manual_con(nodo("p", "documento_plantilla", plantilla="presentacion-comite", salida="p",
                                    desde_ia={"modo": "completo", "instrucciones": "Dos diapositivas"}), tema="x")
        textos = [s.shapes.title.text for s in Presentation(io.BytesIO(self.leer(r["variables"]["p"]))).slides]
        self.assertEqual(textos, ["Portada diciembre", "Logros", "Cifras", "Gracias"])
        # modo llenar sin nada vacío: no llama a la IA y lo avisa
        antes = len(self.modelo.llamadas)
        r, _ = self.manual_con(nodo("p", "documento_plantilla", plantilla="presentacion-comite", datos={"mes": "enero"}, salida="p",
                                    desde_ia={"instrucciones": "x"}), tema="x")
        self.assertEqual(len(self.modelo.llamadas), antes)
        self.assertTrue(any("no tiene variables vacías" in a for a in r["avisos"]))

    def test_powerpoint_desde_texto_con_contenido_en_su_lugar(self):
        datos = m_sal.crear_desde_texto("pptx", "# Comité de {{mes}}\n\n## Logros\n- {{logro}}\n\n{{contenido}}\n\n## Cierre\n- Gracias", "Nombre de la plantilla")
        ctx = m_sal.Contexto(lambda r: {"mes": "noviembre", "logro": "Menos tiempo"}.get(r), contenido={"titulo": "", "diapositivas": [{"titulo": "De la IA", "puntos": ["p"]}]})
        texto = m_sal.texto_pptx(m_sal.llenar_pptx(datos, ctx))
        titulos = [bloque.split("\n")[1] for bloque in texto.split("\n\n") if bloque.startswith("## Diapositiva")]
        self.assertEqual(titulos, ["Comité de noviembre", "Logros", "De la IA", "Cierre"])
        self.assertIn("Menos tiempo", texto)
        # el marcador entre otros textos: se quita y lo de la IA va en la diapositiva siguiente
        from pptx import Presentation
        prs = Presentation()
        s = prs.slides.add_slide(prs.slide_layouts[1])
        s.shapes.title.text = "Resumen"
        s.placeholders[1].text_frame.text = "Antes"
        s.placeholders[1].text_frame.add_paragraph().text = "{{contenido}}"
        b = io.BytesIO()
        prs.save(b)
        ctx = m_sal.Contexto(lambda r: None, contenido={"diapositivas": [{"titulo": "Nueva", "puntos": ["q"]}]})
        texto = m_sal.texto_pptx(m_sal.llenar_pptx(b.getvalue(), ctx))
        self.assertEqual(texto, "## Diapositiva 1\nResumen\nAntes\n\n## Diapositiva 2\nNueva\nq")

    def test_errores_claros(self):
        r, _ = self.correr_orden(self.con(nodo("p", "documento_plantilla", plantilla="no-existe")), exigir_ok=False, entrada={})
        self.assertIn("«no-existe» no existe", r["motivo"])
        self.plantilla("hoja", "xlsx", m_sal.xlsx_de_texto("a | b"))
        r, _ = self.correr_orden(self.con(nodo("p", "documento_plantilla", plantilla="hoja", formato="pdf")), exigir_ok=False, entrada={})
        self.assertIn("pdf solo con plantillas de Word o HTML", r["motivo"])

    def test_crear_desde_texto_y_analizar(self):
        """Lo que usa el servidor (salidas.py crear/analizar/llenar) para la pantalla Documentos y Vera."""
        for formato in ("docx", "pptx", "xlsx", "html"):
            with self.subTest(formato=formato):
                datos = m_sal.crear_desde_texto(formato, "# Hola {{nombre}}\n\n| Concepto | Valor |\n| {{#items}}{{item.concepto}} | {{item.valor}} |\n\n{{contenido}}", "Prueba")
                a = m_sal.analizar(datos, formato)
                self.assertIn("nombre", a["variables"])
                if formato != "html":
                    self.assertEqual(a["repeticiones"], {"items": ["concepto", "valor"]})
                self.assertTrue(a["contenido"])
                ctx = m_sal.Contexto(lambda ref: m_sal._navegar({"nombre": "Ana", "items": [{"concepto": "x", "valor": 1}]}, ref))
                self.assertTrue(m_sal.llenar(formato, datos, ctx))
                self.assertIn("nombre", ctx.usadas)
        ruta = self.estado / "p.docx"
        ruta.write_bytes(self.word())
        p = subprocess.run([sys.executable, str(AQUI / "salidas.py"), "analizar", str(ruta), "docx"], capture_output=True, text=True, encoding="utf-8", timeout=60)
        a = json.loads(p.stdout)
        self.assertEqual(a["prohibidas"], ["entorno.SECRETO"])
        self.assertEqual(a["imagenes"], ["logo"])
        salida = self.estado / "llena.docx"
        p = subprocess.run([sys.executable, str(AQUI / "salidas.py"), "llenar", str(ruta), "docx", str(salida)], input=json.dumps({"datos": {"nombre": "Ana"}}),
                           capture_output=True, text=True, encoding="utf-8", timeout=60)
        r = json.loads(p.stdout)
        self.assertTrue(r["ok"])
        self.assertIn("radicado", r["faltantes"])
        self.assertIn("logo", r["sin_imagen"])


class ValidacionDeSalidas(unittest.TestCase):
    MALOS = [{"tipo": "archivo_exportar", "config": {"formato": "pdf", "de": "x"}},
             {"tipo": "archivo_exportar", "config": {"formato": "json"}},
             {"tipo": "archivo_exportar", "config": {"formato": "json", "de": "x", "contenido": "y"}},
             {"tipo": "archivo_exportar", "config": {"formato": "csv", "de": "x", "separador": "|"}},
             {"tipo": "archivo_exportar", "config": {"formato": "xml", "de": "x", "raiz": "1 malo"}},
             {"tipo": "archivo_comprimir", "config": {}},
             {"tipo": "archivo_comprimir", "config": {"archivos": []}},
             {"tipo": "documento_plantilla", "config": {}},
             {"tipo": "documento_plantilla", "config": {"plantilla": "Con Espacios"}},
             {"tipo": "documento_plantilla", "config": {"plantilla": "carta", "formato": "odt"}},
             {"tipo": "documento_plantilla", "config": {"plantilla": "carta", "desde_ia": {"instrucciones": "x", "modo": "todo"}}},
             {"tipo": "documento_plantilla", "config": {"plantilla": "carta", "desde_ia": {"modo": "llenar"}}},
             {"tipo": "documento_plantilla", "config": {"plantilla": "carta", "datos": {"a b": "x"}}},
             {"tipo": "documento_generar", "config": {"formato": "odp", "plantilla": "x"}}]

    def test_fichas_completas_y_ejemplos_validos(self):
        for t in ns.TIPOS:
            with self.subTest(tipo=t):
                s = nd.TIPOS[t]
                self.assertTrue(s["descripcion"] and len(s["ejemplos"]) >= 2 and len(s["consejos"]) >= 2)
                for c in [s["ejemplo"]["config"], *s["ejemplos"]]:
                    self.assertEqual(fd._validar_config({"id": "n", "tipo": t, "config": c}), [], c)
        self.assertIn("pptx", nd.TIPOS["documento_generar"]["config"]["properties"]["formato"]["enum"])
        self.assertTrue(ns.TIPOS <= fd.TIPOS)
        self.assertFalse(ns.TIPOS & fd.TIPOS_ESCRITURA)  # nada de esto escribe afuera
        self.assertIn("documento_plantilla", nd.DOCUMENTACION)

    def test_python_rechaza_lo_que_no_vale(self):
        for m in self.MALOS:
            with self.subTest(m=m):
                self.assertTrue(fd._validar_config({"id": "n", **m}), m)

    def test_las_variables_se_revisan_en_el_flujo(self):
        d = cadena(nodo("z", "archivo_comprimir", archivos=["{{fantasma}}"]))
        self.assertTrue(any("fantasma" in p for p in fd.validar(d)))
        d = cadena(nodo("x", "archivo_exportar", formato="json", de="datos_que_no_hay"))
        self.assertTrue(any("datos_que_no_hay" in p for p in fd.validar(d)))
        d = cadena(nodo("p", "documento_plantilla", plantilla="carta", datos={"nombre": "{{correo.remitente_nombre}}"}),
                   nodo("z", "archivo_comprimir", archivos=["{{p}}", "adjuntos"]))
        self.assertEqual(fd.validar(d), [])

    def test_zod_del_servidor_en_paridad(self):
        dist = Path(os.environ.get("RUTA_SERVIDOR_DIST", "/app/servidor/dist")) / "flujos.js"
        node = shutil.which("node")
        if not node or not dist.exists():
            self.skipTest("no hay servidor compilado o node")
        buenos = [{"tipo": t, "config": c} for t in sorted(ns.TIPOS | {"documento_generar"}) for c in [nd.TIPOS[t]["ejemplo"]["config"], *nd.TIPOS[t]["ejemplos"]]]
        guion = ("const f = await import(process.argv[1]); const d = JSON.parse(process.argv[2]);"
                 "const r = (xs) => xs.map((e) => { const s = f.CONFIG_DE[e.tipo]; return s ? s.safeParse(e.config).success : null; });"
                 "console.log(JSON.stringify({tipos: [...f.TIPOS_DE_NODO], buenos: r(d.buenos), malos: r(d.malos)}));")
        p = subprocess.run([node, "--input-type=module", "-e", guion, dist.as_uri(), json.dumps({"buenos": buenos, "malos": self.MALOS})],
                           capture_output=True, text=True, encoding="utf-8", timeout=60)
        r = json.loads(p.stdout.strip().splitlines()[-1])
        self.assertTrue(ns.TIPOS <= set(r["tipos"]))
        self.assertEqual([b for b, ok in zip(buenos, r["buenos"]) if not ok], [])
        self.assertEqual([m for m, ok in zip(self.MALOS, r["malos"]) if ok], [])


class VeraConDocumentos(unittest.TestCase):
    def test_herramientas_y_grupo(self):
        import asistente as a
        nombres = {h["name"] for h in a.HERRAMIENTAS}
        self.assertTrue({"plantillas_documento", "crear_plantilla_documento"} <= nombres)
        self.assertTrue({"plantillas_documento", "crear_plantilla_documento"} <= set(a.GRUPOS["construir"]))
        grupos = a._grupos_para("genera un PowerPoint de 3 diapositivas con IA y muéstramelo en el chat", {"vista": "inicio"})
        self.assertTrue({"construir", "correr"} <= set(grupos))
        self.assertIn("documentos", next(h for h in a.HERRAMIENTAS if h["name"] == "abrir_vista")["description"])

    def test_crear_y_listar_por_la_api(self):
        import asistente as a
        llamadas, emitidos = [], []
        api, emitir = a.api, a.emitir
        a.api = lambda m, r, c=None: llamadas.append((m, r, c)) or ({"id": "ev-bienvenida", "nombre": "EV", "formato": "html", "variables": ["nombre"]} if m == "POST" else
                                                                     {"plantillas": [{"id": "x", "nombre": "X", "formato": "docx", "variables": ["a"], "recursos": [{"nombre": "logo"}], "texto": "mucho"}]})
        a.emitir = lambda tipo, **d: emitidos.append((tipo, d))
        try:
            r = a.ejecutar("crear_plantilla_documento", {"nombre": "EV", "formato": "correo", "contenido": "<p>{{nombre}}</p>", "asunto": "Hola"})
            self.assertEqual(llamadas[-1], ("POST", "/api/documentos", {"nombre": "EV", "asunto": "Hola", "formato": "html", "contenido": "<p>{{nombre}}</p>"}))
            self.assertEqual(r["id"], "ev-bienvenida")
            self.assertIn(("control", {"accion": "vista", "vista": "documentos", "documento": "ev-bienvenida"}), emitidos)
            r = a.ejecutar("crear_plantilla_documento", {"nombre": "Informe", "formato": "PowerPoint", "texto": "# {{mes}}"})
            self.assertEqual(llamadas[-1][2]["formato"], "pptx")
            self.assertEqual(llamadas[-1][2]["texto"], "# {{mes}}")
            r = a.ejecutar("plantillas_documento", {"texto": "carta"})
            self.assertEqual(llamadas[-1][:2], ("GET", "/api/documentos?q=carta"))
            self.assertEqual(r["plantillas"][0]["recursos"], ["logo"])
            self.assertNotIn("texto", r["plantillas"][0])
        finally:
            a.api, a.emitir = api, emitir


if __name__ == "__main__":
    unittest.main(verbosity=2)
