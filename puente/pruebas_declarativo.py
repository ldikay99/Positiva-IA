"""
Pruebas del motor de flujos declarativos y de su biblioteca de nodos.

Sin red: la IA es un ModeloFalso que reemplaza `_llamar_modelo`, la visión
queda apagada y el correo es un mensaje.json de ejemplo en una carpeta
temporal. Se corren con

    PYTHONPATH=motor py puente/pruebas_declarativo.py        (desde la raíz)
"""
from __future__ import annotations

import asyncio
import copy
import http.server
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))

import flujo_declarativo as fd  # noqa: E402
import nodos_declarativos as nd  # noqa: E402

CASO = "caso_prueba"
SALTO = chr(10)  # los eventos van uno por línea; splitlines() cortaría en el separador (0x1E) de la marca
CUERPO_HTML = ("<p>Buenos días,</p><p>Solicito el pago de la incapacidad de Ana María Pérez, cédula 1.023.456.789, "
               "radicado 11001310300120240012300.</p><p>Gracias</p>")
CERTIFICADO = "CERTIFICADO DE INCAPACIDAD\nPaciente: Ana María Pérez\nDocumento: 1023456789\nDías: 5\nDiagnóstico: M54.5\n"


# ------------------------------------------------------------- el modelo falso

class ModeloFalso:
    """Responde según lo que se le pide y guarda cada llamada para revisarla."""

    def __init__(self, **respuestas):
        self.r = {"categoria": "incapacidad", "decision": "urgente", "justificacion": "hay hospitalización",
                  "campos": {"nombre": "Ana María Pérez", "documento": "1023456789"}, "json": {"saludo": "hola", "prioridad": "alta"},
                  **respuestas}
        self.llamadas = []
        self.fallos = []  # excepciones que lanzan las próximas llamadas, en orden

    def __call__(self, modelo, sistema, tarea, esquema, temperatura, json_libre=False):
        self.llamadas.append({"modelo": modelo, "sistema": sistema, "tarea": tarea, "esquema": esquema, "temperatura": temperatura, "json": json_libre})
        if self.fallos:
            raise self.fallos.pop(0)
        tok = {"entrada": 10, "salida": 5}
        props = (esquema or {}).get("properties") or {}
        if "categoria" in props:
            return json.dumps({"categoria": self.r["categoria"], "confianza": 0.9, "evidencia": "Solicito el pago de la incapacidad"}), tok
        if "decision" in props:
            return json.dumps({"decision": self.r["decision"], "justificacion": self.r["justificacion"], "confianza": 0.8}), tok
        if props and all((v.get("properties") or {}).get("valor") for v in props.values()):
            return json.dumps({k: {"valor": self.r["campos"].get(k, ""), "confianza": 0.95, "evidencia": f"«{self.r['campos'].get(k, '')}»"} for k in props}), tok
        if esquema or json_libre:
            return "```json\n" + json.dumps(self.r["json"]) + "\n```", tok
        return f"TEXTO GENERADO a partir de:\n{tarea}", tok


# ------------------------------------------------------------- utilidades

def flujo(nodos, aristas, **extra):
    return {"id": "prueba", "nombre": "Prueba", "modelo": "gemini-falso", "nodos": nodos, "aristas": aristas, **extra}


def nodo(id_, tipo, **config):
    return {"id": id_, "tipo": tipo, "nombre": id_, "config": config}


def cadena(*nodos, fin=None):
    """Un flujo lineal que termina en una salida «manual»."""
    fin = fin or nodo("fin", "salida", desenlace="manual")
    ns = [nodo("correo", "entrada_correo")] + list(nodos) + [fin]
    return flujo(ns, [{"desde": a["id"], "hacia": b["id"]} for a, b in zip(ns, ns[1:])])


INCAPACIDAD = {  # el flujo real guardado en la plataforma
    "id": "solicitudes-de-incapacidad", "nombre": "Solicitudes de incapacidad", "modelo": "gemini-2.5-flash",
    "nodos": [
        {"id": "correo", "tipo": "entrada_correo", "nombre": "Correo recibido", "config": {"ocr": True, "leer_adjuntos": True}},
        {"id": "tema", "tipo": "ia_clasificar", "nombre": "¿De qué trata?", "config": {"categorias": [
            {"valor": "incapacidad", "descripcion": "pide pago"}, {"valor": "otro", "descripcion": "otra cosa"}], "variable": "tema", "instrucciones": "Clasifica."}},
        {"id": "datos", "tipo": "ia_extraer", "nombre": "Datos", "config": {"campos": [
            {"obligatorio": True, "descripcion": "nombre", "nombre": "nombre_trabajador"},
            {"descripcion": "cédula", "nombre": "documento", "tipo": "numero", "obligatorio": True}]}},
        {"id": "revisar", "tipo": "validar", "nombre": "Revisar", "config": {"reglas": [
            {"regla": "obligatorio", "codigo": "SIN_NOMBRE", "campo": "nombre_trabajador"},
            {"campo": "documento", "valor": "[0-9]{6,10}", "codigo": "DOCUMENTO_INVALIDO", "regla": "patron"}]}},
        {"id": "radicar", "tipo": "salida", "nombre": "Radicar", "config": {"campos": ["nombre_trabajador", "documento"], "desenlace": "radicar"}},
        {"id": "manual", "tipo": "salida", "nombre": "Manual", "config": {"desenlace": "manual"}},
        {"id": "otro_tramite", "tipo": "salida", "nombre": "Otro", "config": {"desenlace": "descartar", "motivo": "NO_ES_INCAPACIDAD"}},
    ],
    "aristas": [{"desde": "correo", "hacia": "tema"}, {"desde": "tema", "hacia": "datos", "cuando": "incapacidad"},
                {"desde": "tema", "hacia": "otro_tramite"}, {"desde": "datos", "hacia": "revisar"},
                {"desde": "revisar", "hacia": "radicar", "cuando": "ok"}, {"desde": "revisar", "hacia": "manual", "cuando": "falla"}],
}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        raiz = Path(self.tmp.name)
        carpeta = raiz / CASO
        (carpeta / "adj").mkdir(parents=True)
        adjuntos = [{"name": "certificado.txt", "archivo": "a1.txt"}, {"name": "logo.png", "archivo": "", "isInline": True}]
        (carpeta / "adj" / "a1.txt").write_text(CERTIFICADO, encoding="utf-8")
        try:
            import fitz  # PyMuPDF, el mismo que usa la conversión

            doc = fitz.open()
            for i in ("UNO", "DOS", "TRES"):
                doc.new_page().insert_text((72, 72), f"Contenido de la PAGINA {i} del soporte")
            (carpeta / "adj" / "a2.pdf").write_bytes(doc.tobytes())
            adjuntos.append({"name": "soporte.pdf", "archivo": "a2.pdf"})
            self.hay_pdf = True
        except Exception:
            self.hay_pdf = False
        (carpeta / "mensaje.json").write_text(json.dumps({
            "subject": "Solicitud pago incapacidad", "receivedDateTime": "2026-11-05T10:00:00Z",
            "from": {"emailAddress": {"name": "Empresa Ejemplo", "address": "rrhh@empresa.example"}},
            "body": {"contentType": "html", "content": CUERPO_HTML}, "_adjuntos": adjuntos}), encoding="utf-8")
        self._orig = (fd.BANDEJA, fd.VIVO, fd._llamar_modelo, fd._transcriptor, fd._salida, fd.RAIZ)
        fd.BANDEJA, fd.VIVO = raiz, raiz / "no_existe"
        fd._transcriptor = lambda: None
        self.modelo = ModeloFalso()
        fd._llamar_modelo = self.modelo
        self.salida = io.StringIO()
        fd._salida = self.salida
        # sin esperas de verdad: se anota cuánto se habría dormido
        self.esperas = []
        self._orig_red = (fd._dormir, set(fd._IPS_PERMITIDAS_PRUEBAS), os.environ.get("HTTP_HOSTS_PERMITIDOS"))
        fd._dormir = self.esperas.append
        os.environ.pop("HTTP_HOSTS_PERMITIDOS", None)

    def tearDown(self):
        fd.BANDEJA, fd.VIVO, fd._llamar_modelo, fd._transcriptor, fd._salida, fd.RAIZ = self._orig
        fd._dormir, ips, hosts = self._orig_red
        fd._IPS_PERMITIDAS_PRUEBAS.clear()
        fd._IPS_PERMITIDAS_PRUEBAS.update(ips)
        if hosts is None:
            os.environ.pop("HTTP_HOSTS_PERMITIDOS", None)
        else:
            os.environ["HTTP_HOSTS_PERMITIDOS"] = hosts
        self.tmp.cleanup()

    def correr_crudo(self, defi):
        """Corre sin exigir que salga bien: (resultado, eventos)."""
        self.salida.seek(0)
        self.salida.truncate()
        asyncio.run(fd.procesar({"cmd": "procesar", "corrida": "x", "caso": CASO, "definicion": defi}))
        eventos = [json.loads(linea[len(fd.MARCA):]) for linea in self.salida.getvalue().split(SALTO) if linea.startswith(fd.MARCA)]
        return next(e for e in eventos if e["tipo"] == "resultado"), eventos

    def correr(self, defi):
        resultado, eventos = self.correr_crudo(defi)
        self.assertNotEqual(resultado["accion"], "error", resultado.get("motivo", "") + "\n" + str(resultado.get("avisos")) + str(resultado.get("traza", "")))
        return resultado, eventos

    def problemas(self, defi):
        return fd.validar(defi)

    def assertProblema(self, defi, texto):
        p = self.problemas(defi)
        self.assertTrue(any(texto in x for x in p), f"se esperaba «{texto}» en {p}")


# ------------------------------------------------------------- validación

class Validacion(Base):
    def test_flujos_existentes_siguen_validos(self):
        self.assertEqual(self.problemas(INCAPACIDAD), [])
        self.assertEqual(self.problemas(nd.EJEMPLO_FLUJO), [])

    def test_ejemplo_de_cada_tipo_tiene_config_valida(self):
        for tipo, t in nd.TIPOS.items():
            with self.subTest(tipo=tipo):
                self.assertEqual(t["ejemplo"]["tipo"], tipo)
                self.assertEqual(fd._validar_config(t["ejemplo"]), [])

    def test_config_obligatoria_por_tipo(self):
        casos = {
            "filtro": ({}, "necesita campo"), "ia_clasificar": ({"categorias": ["a"]}, "variable"),
            "ia_decidir": ({"pregunta": "¿?", "opciones": [{"valor": "a", "criterio": "x"}]}, "al menos dos opciones"),
            "ia_extraer": ({}, "necesita campos"), "extraer_regex": ({"campos": [{"nombre": "x", "patron": "("}]}, "patrón inválido"),
            "validar": ({"reglas": [{"campo": "x", "regla": "rara"}]}, "desconocida"),
            "transformar": ({"operaciones": [{"op": "mayusculas", "salida": "x"}]}, "necesita de"),
            "buscar_en_tabla": ({"buscar": "x", "columna": "c"}, "necesita tabla"),
            "bifurcar_por_valor": ({"variable": "x", "casos": ["a", "otro"]}, "«otro»"),
            "ia_generar": ({}, "necesita instrucciones"), "notificar": ({"cuerpo": "x"}, "necesita para"),
            "enviar": ({}, "necesita cuerpo"), "salida": ({"desenlace": "volar"}, "desenlace"),
            "leer_adjuntos": ({"max_paginas": 500, "tipos": ["exe"]}, "max_paginas"),
        }
        for tipo, (conf, texto) in casos.items():
            with self.subTest(tipo=tipo):
                p = fd._validar_config(nodo("n", tipo, **conf))
                self.assertTrue(any(texto in x for x in p), f"{tipo}: {p}")
        p = fd._validar_config(nodo("n", "ia_generar", instrucciones="x", temperatura=3, salida="correo"))
        self.assertTrue(any("temperatura" in x for x in p) and any("nombre de variable" in x for x in p), p)

    def test_variables_tienen_que_existir_antes(self):
        d = cadena(nodo("gen", "ia_generar", instrucciones="Responde a {{nombre}}", salida="respuesta"))
        self.assertProblema(d, "ningún nodo anterior produce la variable «nombre»")
        # producida después no vale
        d = cadena(nodo("gen", "ia_generar", instrucciones="Usa {{datos}}"), nodo("x", "ia_extraer", salida="datos", campos=[{"nombre": "a"}]))
        self.assertProblema(d, "«datos»")
        # producida antes sí; los datos del correo siempre existen
        d = cadena(nodo("x", "ia_extraer", salida="datos", campos=[{"nombre": "a"}]),
                   nodo("gen", "ia_generar", instrucciones="Usa {{datos.a}}, {{a}}, {{correo.asunto}} y {{adjuntos.texto}}"))
        self.assertEqual(self.problemas(d), [])
        self.assertProblema(cadena(nodo("gen", "ia_generar", instrucciones="{{correo.telefono}}")), "«correo.telefono» no existe")
        self.assertProblema(cadena(nodo("gen", "ia_generar", instrucciones="x", entradas=["fantasma"])), "«fantasma»")
        self.assertProblema(cadena(fin=nodo("fin", "salida", desenlace="manual", campos=["nada"])), "«nada»")
        self.assertProblema(cadena(nodo("gen", "ia_generar", instrucciones="hola {{x")), "plantilla mal cerrada")

    def test_basta_con_que_exista_en_algun_camino(self):
        d = flujo([nodo("correo", "entrada_correo"), nodo("f", "filtro", campo="asunto", valor="pago"),
                   nodo("x", "transformar", operaciones=[{"op": "asignar", "valor": "sí", "salida": "marca"}]),
                   nodo("gen", "ia_generar", instrucciones="{{marca}}"), nodo("fin", "salida", desenlace="manual")],
                  [{"desde": "correo", "hacia": "f"}, {"desde": "f", "hacia": "x", "cuando": "si"}, {"desde": "f", "hacia": "gen", "cuando": "no"},
                   {"desde": "x", "hacia": "gen"}, {"desde": "gen", "hacia": "fin"}])
        self.assertEqual(self.problemas(d), [])

    def test_operaciones_de_transformar_se_encadenan(self):
        d = cadena(nodo("t", "transformar", operaciones=[{"op": "asignar", "valor": "a", "salida": "x"}, {"op": "mayusculas", "de": "x", "salida": "y"}]))
        self.assertEqual(self.problemas(d), [])
        d = cadena(nodo("t", "transformar", operaciones=[{"op": "mayusculas", "de": "y", "salida": "x"}, {"op": "asignar", "valor": "a", "salida": "y"}]))
        self.assertProblema(d, "«y»")

    def test_aristas_por_cada_salida(self):
        d = copy.deepcopy(INCAPACIDAD)
        d["aristas"] = [a for a in d["aristas"] if not (a["desde"] == "tema" and "cuando" not in a)]
        self.assertProblema(d, "falta arista para la salida «otro»")
        d["aristas"].append({"desde": "tema", "hacia": "otro_tramite", "cuando": "Otro"})  # sin distinguir mayúsculas
        self.assertEqual(self.problemas(d), [])
        d = copy.deepcopy(INCAPACIDAD)
        d["aristas"] = [a for a in d["aristas"] if a.get("cuando") != "falla"]
        self.assertProblema(d, "«falla»")
        d = cadena(nodo("b", "bifurcar_por_valor", variable="asunto", casos=["a", "b"]))
        self.assertEqual(self.problemas(d), [])  # la arista por defecto cubre a, b y otro
        d["aristas"][1]["cuando"] = "a"
        self.assertProblema(d, "«b», «otro»")

    def test_aristas_que_nunca_se_toman_no_impiden_correr(self):
        d = copy.deepcopy(INCAPACIDAD)
        d["aristas"].append({"desde": "revisar", "hacia": "manual", "cuando": "quizas"})
        self.assertProblema(d, "nunca ocurre")
        self.assertEqual(fd.validar(d, al_ejecutar=True), [])

    def test_ciclos_alcance_y_salidas(self):
        d = cadena(nodo("a", "transformar", op="asignar", valor="1", salida="x"), nodo("b", "condicion", campo="x", operador="no_vacio"))
        d["aristas"] = [x for x in d["aristas"] if x["desde"] != "b"] + [{"desde": "b", "hacia": "a", "cuando": "si"}, {"desde": "b", "hacia": "fin", "cuando": "no"}]
        self.assertProblema(d, "hay un ciclo: a → b → a")
        d = cadena()
        d["nodos"].append(nodo("suelto", "salida", desenlace="manual"))
        self.assertProblema(d, "suelto: no se llega desde la entrada")
        d = cadena()
        d["aristas"].append({"desde": "fin", "hacia": "correo"})
        self.assertProblema(d, "es una salida y no puede tener aristas")
        self.assertProblema(flujo([nodo("fin", "salida", desenlace="manual")], []), "exactamente un disparador")
        self.assertProblema(cadena(nodo("z", "teletransportar")), "tipo desconocido")

    def test_tabla_csv(self):
        fd.RAIZ = Path(self.tmp.name)
        (fd.RAIZ / "tablas").mkdir()
        (fd.RAIZ / "tablas" / "areas.csv").write_text("tema;area\nincapacidad;Prestaciones\n", encoding="utf-8")
        ok = cadena(nodo("t", "buscar_en_tabla", buscar="asunto", archivo="tablas/areas.csv", columna="tema", traer=["area"]))
        self.assertEqual(self.problemas(ok), [])
        self.assertProblema(cadena(nodo("t", "buscar_en_tabla", buscar="asunto", archivo="tablas/areas.csv", columna="nope")), "la columna «nope»")
        self.assertProblema(cadena(nodo("t", "buscar_en_tabla", buscar="asunto", archivo="tablas/no.csv", columna="tema")), "no existe el archivo")
        self.assertProblema(cadena(nodo("t", "buscar_en_tabla", buscar="asunto", archivo="../fuera.csv", columna="tema")), "dentro del repositorio")

    def test_cli_validar(self):
        r = subprocess.run([sys.executable, str(AQUI / "flujo_declarativo.py"), "validar"], input=json.dumps(cadena(nodo("g", "ia_generar"))),
                           capture_output=True, text=True, encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout=120)
        problemas = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertTrue(any("instrucciones" in p for p in problemas), r.stdout + r.stderr)


# ------------------------------------------------------------- plantillas

class Plantillas(Base):
    def test_render(self):
        e = fd.Ejecucion(cadena(), CASO, "m")
        e.entrada_correo(nodo("correo", "entrada_correo", leer_adjuntos=False))
        e.vars.update({"datos": {"nombre": "Ana", "dias": 5}, "lista": ["a", "b"], "vacio": None})
        self.assertEqual(e.render("{{correo.asunto}}"), "Solicitud pago incapacidad")
        self.assertEqual(e.render("{{ correo.remitente }}"), "Empresa Ejemplo <rrhh@empresa.example>")
        self.assertEqual(e.render("{{correo.remitente_nombre}}|{{correo.remitente_correo}}"), "Empresa Ejemplo|rrhh@empresa.example")
        self.assertIn("1.023.456.789", e.render("{{correo.cuerpo}}"))
        self.assertNotIn("<p>", e.render("{{correo.cuerpo}}"))
        self.assertEqual(e.render("{{adjuntos.nombres}}"), "certificado.txt" + (", soporte.pdf" if self.hay_pdf else ""))
        self.assertEqual(e.render("{{adjuntos.texto}}"), "")  # no se leyeron
        self.assertEqual(e.render("Hola {{datos.nombre}}, {{datos.dias}} días; {{lista}}; [{{vacio}}][{{no_hay}}]"), "Hola Ana, 5 días; a, b; [][]")
        self.assertEqual(json.loads(e.render("{{datos}}")), {"nombre": "Ana", "dias": 5})
        self.assertEqual(e.texto_de("datos.nombre"), "Ana")
        self.assertEqual(e.texto_de("asunto"), "Solicitud pago incapacidad")  # nombre corto de antes

    def test_entradas_limitan_lo_que_ve_la_ia(self):
        d = cadena(nodo("t", "transformar", op="asignar", valor="DATO-SECRETO-NO", salida="otro"),
                   nodo("g", "ia_generar", instrucciones="Escribe a {{correo.remitente_nombre}}", entradas=["correo.asunto", "{{adjuntos.texto}}"], salida="r"))
        self.correr(d)
        ll = self.modelo.llamadas[-1]
        self.assertIn("correo.asunto:\nSolicitud pago incapacidad", ll["tarea"])
        self.assertIn("CERTIFICADO DE INCAPACIDAD", ll["tarea"])  # la entrada lee los adjuntos (no hay leer_adjuntos)
        self.assertNotIn("1.023.456.789", ll["tarea"])  # el cuerpo no se pidió
        self.assertNotIn("DATO-SECRETO-NO", ll["tarea"])
        self.assertIn("Escribe a Empresa Ejemplo", ll["sistema"])
        # sin entradas ve todo
        self.correr(cadena(nodo("g", "ia_generar", instrucciones="x")))
        self.assertIn("1.023.456.789", self.modelo.llamadas[-1]["tarea"])


# ------------------------------------------------------------- ejecución

class Ejecucion(Base):
    def test_flujo_de_incapacidad_de_antes(self):
        # el modelo no encuentra el nombre: queda vacío (no se inventa) y la validación lo manda a manual
        r, ev = self.correr(INCAPACIDAD)
        self.assertEqual((r["accion"], r["motivo"]), ("manual", "SIN_NOMBRE"))
        self.assertIn("nombre_trabajador", r["sin_resolver"])
        self.modelo.r["campos"] = {"nombre_trabajador": "Ana María Pérez", "documento": "1023456789"}
        r, ev = self.correr(INCAPACIDAD)
        self.assertEqual(r["accion"], "radicado")
        self.assertEqual(r["solicitud"], {"nombre_trabajador": "Ana María Pérez", "documento": "1023456789"})
        self.assertEqual(r["radicado"], "SIMULADO")
        self.modelo.r["categoria"] = "otro"
        r, _ = self.correr(INCAPACIDAD)
        self.assertEqual((r["accion"], r["motivo"]), ("descartado", "NO_ES_INCAPACIDAD"))

    def test_ejemplo_completo_con_eventos(self):
        r, ev = self.correr(nd.EJEMPLO_FLUJO)
        self.assertEqual(r["accion"], "respondido")
        self.assertEqual(r["solicitud"], {"nombre": "Ana María Pérez", "documento": "1023456789"})
        self.assertTrue(r["respuesta"].startswith("TEXTO GENERADO"))
        self.assertIn("Ana María Pérez", r["respuesta"])  # generó a partir de «datos»
        self.assertEqual(len(r["mensajes"]), 1)
        m = r["mensajes"][0]
        self.assertEqual((m["para"], m["asunto"], m["enviado"], m["bloqueado"]), ("mesa@ejemplo.gov.co", "Caso de Ana María Pérez", False, True))
        tipos = [e["tipo"] for e in ev]
        for t in ("inicio", "etapa_inicio", "etapa_fin", "paso", "modelo_inicio", "modelo_fin", "busqueda", "escritura", "resultado"):
            self.assertIn(t, tipos)
        esc = next(e for e in ev if e["tipo"] == "escritura")
        self.assertTrue(esc["bloqueada"] and esc["simulada"])
        self.assertEqual(esc["operacion"], "notificar")
        mi = next(e for e in ev if e["tipo"] == "modelo_inicio" and e["quien"] == "respuesta")
        self.assertIn("Redacta una respuesta", mi["sistema"])
        self.assertEqual(mi["temperatura"], 0.4)
        mf = next(e for e in ev if e["tipo"] == "modelo_fin" and e["quien"] == "respuesta")
        self.assertTrue(mf["respuesta"].startswith("TEXTO GENERADO"))
        self.assertEqual({e["campo"] for e in ev if e["tipo"] == "busqueda"}, {"nombre", "documento"})
        etapas = [e["etapa"] for e in ev if e["tipo"] == "etapa_fin"]
        self.assertEqual(etapas, ["correo", "adjuntos", "datos", "respuesta", "avisar", "fin"])
        self.assertTrue(all(e.get("titulo") for e in ev if e["tipo"] == "paso"))
        # la entrada no leyó adjuntos: los leyó el nodo leer_adjuntos y solo PDF/imagen
        pasos = [(e["nodo"], e["titulo"]) for e in ev if e["tipo"] == "paso"]
        self.assertIn(("adjuntos", "certificado.txt: se omite"), pasos)
        self.assertFalse(any(n == "correo" and "leído" in t for n, t in pasos))

    def test_entrada_lee_o_no_lee_adjuntos(self):
        _, ev = self.correr(cadena())
        self.assertTrue(any(e["tipo"] == "paso" and e["titulo"] == "certificado.txt: leído" for e in ev))
        _, ev = self.correr(cadena(fin=nodo("fin", "salida", desenlace="manual"))
                            | {"nodos": [nodo("correo", "entrada_correo", leer_adjuntos=False), nodo("fin", "salida", desenlace="manual")]})
        self.assertFalse(any(e["tipo"] == "paso" and "leído" in e["titulo"] for e in ev))

    def test_leer_adjuntos_tipos_paginas_y_sin_adjuntos(self):
        d = cadena(nodo("adj", "leer_adjuntos", tipos=["texto"], ocr=False, salida="texto_adj"),
                   nodo("g", "ia_generar", instrucciones="x", entradas=["texto_adj"]))
        r, ev = self.correr(d)
        self.assertIn("CERTIFICADO", r["variables"]["texto_adj"])
        self.assertNotIn("PAGINA", r["variables"]["texto_adj"])
        if self.hay_pdf:
            d = cadena(nodo("adj", "leer_adjuntos", tipos=[".pdf"], max_paginas=1, salida="t"))
            r, _ = self.correr(d)
            self.assertIn("PAGINA UNO", r["variables"]["t"])
            self.assertNotIn("PAGINA DOS", r["variables"]["t"])
            from pipeline import conversion
            self.assertEqual(conversion.LIMITE_PAGINAS_PDF, 60)  # se restituye
        # ninguno coincide → sale por sin_adjuntos
        d = flujo([nodo("correo", "entrada_correo"), nodo("adj", "leer_adjuntos", nombre_patron="^factura"),
                   nodo("si", "salida", desenlace="radicar"), nodo("no", "salida", desenlace="manual", motivo="SIN_SOPORTES")],
                  [{"desde": "correo", "hacia": "adj"}, {"desde": "adj", "hacia": "si", "cuando": "ok"}, {"desde": "adj", "hacia": "no", "cuando": "sin_adjuntos"}])
        r, _ = self.correr(d)
        self.assertEqual((r["accion"], r["motivo"]), ("manual", "SIN_SOPORTES"))

    def test_filtro_y_condicion(self):
        def con(n):
            return flujo([nodo("correo", "entrada_correo", leer_adjuntos=False), n, nodo("si", "salida", desenlace="radicar"), nodo("no", "salida", desenlace="descartar")],
                         [{"desde": "correo", "hacia": n["id"]}, {"desde": n["id"], "hacia": "si", "cuando": "sí"}, {"desde": n["id"], "hacia": "no", "cuando": "no"}])
        self.assertEqual(self.correr(con(nodo("f", "filtro", campo="asunto", valor="INCAPACIDAD")))[0]["accion"], "radicado")
        self.assertEqual(self.correr(con(nodo("f", "filtro", campo="remitente", operador="coincide", valor=r"@juzgado\.")))[0]["accion"], "descartado")
        self.assertEqual(self.correr(con(nodo("f", "filtro", campo="correo.remitente_correo", operador="igual", valor="RRHH@empresa.example")))[0]["accion"], "radicado")
        self.assertEqual(self.correr(con(nodo("f", "condicion", campo="adjuntos.texto", operador="vacio")))[0]["accion"], "radicado")

    def test_ia_clasificar_con_salida_modelo_y_temperatura(self):
        d = cadena(nodo("c", "ia_clasificar", categorias=["incapacidad", "otro"], salida="tema", modelo="gemini-otro", temperatura=0))
        r, ev = self.correr(d)
        self.assertEqual(r["variables"]["tema"], "incapacidad")
        self.assertEqual((self.modelo.llamadas[-1]["modelo"], self.modelo.llamadas[-1]["temperatura"]), ("gemini-otro", 0.0))

    def test_ia_decidir(self):
        d = flujo([nodo("correo", "entrada_correo", leer_adjuntos=False),
                   nodo("u", "ia_decidir", pregunta="¿Es urgente el caso de {{correo.remitente_nombre}}?", salida="urgencia",
                        opciones=[{"valor": "urgente", "criterio": "hay hospitalización"}, {"valor": "normal", "criterio": "lo demás"}]),
                   nodo("g", "ia_generar", instrucciones="Explica: {{urgencia_justificacion}}"),
                   nodo("rapido", "salida", desenlace="reenviar", destino="urgencias"), nodo("lento", "salida", desenlace="manual")],
                  [{"desde": "correo", "hacia": "u"}, {"desde": "u", "hacia": "g", "cuando": "urgente"}, {"desde": "u", "hacia": "lento", "cuando": "normal"},
                   {"desde": "g", "hacia": "rapido"}])
        self.assertEqual(self.problemas(d), [])
        r, ev = self.correr(d)
        self.assertEqual((r["accion"], r["area_destino"]), ("reenviado", "urgencias"))
        self.assertEqual(r["variables"]["urgencia_justificacion"], "hay hospitalización")
        self.assertIn("¿Es urgente el caso de Empresa Ejemplo?", self.modelo.llamadas[0]["sistema"])
        self.assertIn("Explica: hay hospitalización", self.modelo.llamadas[1]["sistema"])
        self.modelo.r["decision"] = "normal"
        self.assertEqual(self.correr(d)[0]["accion"], "manual")

    def test_extraer_regex(self):
        d = cadena(nodo("rx", "extraer_regex", campos=[
            {"nombre": "radicado", "patron": r"\b(\d{23})\b", "obligatorio": True},
            {"nombre": "cedula", "patron": r"c[eé]dula\s+([\d.]+)"},
            {"nombre": "cie10", "patron": r"\b[A-Z]\d{2}(?:\.\d)?\b", "todos": True}]))
        r, ev = self.correr(d)
        v = r["variables"]
        self.assertEqual((v["radicado"], v["cedula"], v["cie10"]), ("11001310300120240012300", "1.023.456.789", ["M54.5"]))
        self.assertEqual(r["atribucion"]["radicado"], "regex")
        self.assertTrue(all(e["metodo"] == "regex" for e in ev if e["tipo"] == "busqueda"))
        d = flujo([nodo("correo", "entrada_correo"), nodo("rx", "extraer_regex", de="{{correo.asunto}}", patron=r"\d{5}", salida="num", obligatorio=True),
                   nodo("ok", "salida", desenlace="radicar"), nodo("falta", "salida", desenlace="manual", motivo="SIN_NUMERO")],
                  [{"desde": "correo", "hacia": "rx"}, {"desde": "rx", "hacia": "ok", "cuando": "ok"}, {"desde": "rx", "hacia": "falta", "cuando": "incompleto"}])
        self.assertEqual(self.problemas(d), [])
        self.assertEqual(self.correr(d)[0]["motivo"], "SIN_NUMERO")

    def test_validar_falla_y_motivo(self):
        self.modelo.r["campos"] = {"nombre_trabajador": "Ana", "documento": "12ab"}
        r, ev = self.correr(INCAPACIDAD)
        self.assertEqual((r["accion"], r["motivo"]), ("manual", "DOCUMENTO_INVALIDO"))
        self.assertTrue(any(e["tipo"] == "paso" and e.get("veredicto") == "rechazado" for e in ev))

    def test_transformar_todas_las_operaciones(self):
        d = cadena(nodo("t", "transformar", operaciones=[
            {"op": "asignar", "valor": "  ana   maría  pérez ", "salida": "crudo"},
            {"op": "limpiar", "de": "crudo", "salida": "limpio"},
            {"op": "titulo", "de": "limpio", "salida": "nombre"},
            {"op": "mayusculas", "de": "nombre", "salida": "may"},
            {"op": "minusculas", "de": "may", "salida": "min"},
            {"op": "solo_digitos", "de": "{{correo.cuerpo}}", "salida": "digitos"},
            {"op": "regex_extraer", "de": "correo.cuerpo", "patron": r"c[eé]dula ([\d.]+)", "salida": "cedula"},
            {"op": "regex_extraer", "de": "correo.cuerpo", "patron": r"pasaporte (\w+)", "defecto": "sin pasaporte", "salida": "pasaporte"},
            {"op": "regex_reemplazar", "de": "cedula", "patron": r"\.", "reemplazo": "", "salida": "cedula"},
            {"op": "concatenar", "de": ["nombre", "{{cedula}}", "vacia"], "separador": " - ", "salida": "titular"},
            {"op": "defecto", "de": "vacia", "valor": "N/D {{correo.asunto}}", "salida": "vacia"},
            {"op": "plantilla", "plantilla": "{{nombre}} (CC {{cedula}})", "salida": "etiqueta"}]))
        # «vacia» se usa antes de existir → la validación lo marca
        self.assertProblema(d, "«vacia»")
        d["nodos"][1]["config"]["operaciones"].insert(0, {"op": "asignar", "valor": "", "salida": "vacia"})
        self.assertEqual(self.problemas(d), [])
        v = self.correr(d)[0]["variables"]
        self.assertEqual(v["limpio"], "ana maría pérez")
        self.assertEqual(v["nombre"], "Ana María Pérez")
        self.assertEqual((v["may"], v["min"]), ("ANA MARÍA PÉREZ", "ana maría pérez"))
        self.assertTrue(v["digitos"].startswith("1023456789"))
        self.assertEqual((v["cedula"], v["pasaporte"]), ("1023456789", "sin pasaporte"))
        self.assertEqual(v["titular"], "Ana María Pérez - 1023456789")
        self.assertEqual(v["vacia"], "N/D Solicitud pago incapacidad")
        self.assertEqual(v["etiqueta"], "Ana María Pérez (CC 1023456789)")

    def test_buscar_en_tabla_embebida_y_csv(self):
        tabla = [{"tema": "Incapacidad", "area": "Prestaciones", "correo": "prest@ejemplo.gov.co"}, {"tema": "afiliación", "area": "Afiliaciones", "correo": "af@x"}]
        d = flujo([nodo("correo", "entrada_correo", leer_adjuntos=False),
                   nodo("c", "ia_clasificar", categorias=["incapacidad", "afiliacion"], variable="tema"),
                   nodo("b", "buscar_en_tabla", buscar="tema", tabla=tabla, columna="tema", traer=["area", "correo"], salida="area"),
                   nodo("n", "notificar", para="{{area.correo}}", asunto="Caso para {{area.area}}", cuerpo="{{correo.asunto}}"),
                   nodo("ok", "salida", desenlace="reenviar", destino="{{area.area}}"), nodo("no", "salida", desenlace="manual")],
                  [{"desde": "correo", "hacia": "c"}, {"desde": "c", "hacia": "b"}, {"desde": "b", "hacia": "n", "cuando": "encontrado"},
                   {"desde": "b", "hacia": "no", "cuando": "no_encontrado"}, {"desde": "n", "hacia": "ok"}])
        self.assertEqual(self.problemas(d), [])
        r, ev = self.correr(d)
        self.assertEqual((r["accion"], r["area_destino"]), ("reenviado", "Prestaciones"))
        self.assertEqual(r["mensajes"][0]["para"], "prest@ejemplo.gov.co")
        self.assertTrue(any(e["tipo"] == "busqueda" and e["campo"] == "area.area" and e["valor"] == "Prestaciones" for e in ev))
        self.modelo.r["categoria"] = "afiliacion"  # sin tilde encuentra «afiliación»
        self.assertEqual(self.correr(d)[0]["area_destino"], "Afiliaciones")
        # CSV del repositorio (aquí, la carpeta temporal hace de repositorio)
        fd.RAIZ = Path(self.tmp.name)
        (fd.RAIZ / "areas.csv").write_text("tema,area\nincapacidad,Prestaciones CSV\n", encoding="utf-8")
        d["nodos"][2]["config"] = {"buscar": "{{tema}}", "archivo": "areas.csv", "columna": "tema", "modo": "contiene", "salida": "area"}
        d["nodos"][3]["config"]["para"] = "mesa@x"
        self.modelo.r["categoria"] = "incapacidad"
        self.assertEqual(self.problemas(d), [])
        self.assertEqual(self.correr(d)[0]["area_destino"], "Prestaciones CSV")
        self.modelo.r["categoria"] = "afiliacion"
        self.assertEqual(self.correr(d)[0]["accion"], "manual")

    def test_bifurcar_por_valor(self):
        d = flujo([nodo("correo", "entrada_correo", leer_adjuntos=False), nodo("c", "ia_clasificar", categorias=["incapacidad", "afiliacion", "queja"], variable="tema"),
                   nodo("b", "bifurcar_por_valor", variable="tema", casos=["Incapacidad", "afiliacion"]),
                   nodo("s1", "salida", desenlace="radicar"), nodo("s2", "salida", desenlace="reenviar"), nodo("s3", "salida", desenlace="manual")],
                  [{"desde": "correo", "hacia": "c"}, {"desde": "c", "hacia": "b"}, {"desde": "b", "hacia": "s1", "cuando": "incapacidad"},
                   {"desde": "b", "hacia": "s2", "cuando": "afiliacion"}, {"desde": "b", "hacia": "s3", "cuando": "otro"}])
        self.assertEqual(self.problemas(d), [])
        self.assertEqual(self.correr(d)[0]["accion"], "radicado")
        self.modelo.r["categoria"] = "queja"
        r, ev = self.correr(d)
        self.assertEqual(r["accion"], "manual")
        self.assertEqual(next(e for e in ev if e["tipo"] == "etapa_fin" and e["etapa"] == "b")["salida"], "otro")

    def test_ia_generar_json_con_esquema(self):
        d = cadena(nodo("g", "ia_generar", instrucciones="Arma la ficha", esquema={"saludo": "un saludo", "prioridad": "alta|baja"}, salida="ficha"),
                   nodo("g2", "ia_generar", instrucciones="Libre", formato="json", salida="libre"),
                   fin=nodo("fin", "salida", desenlace="responder", campos={"prioridad": "{{ficha.prioridad}}"}, plantilla_respuesta="{{ficha.saludo}}!"))
        self.assertEqual(self.problemas(d), [])
        r, _ = self.correr(d)
        self.assertEqual(r["variables"]["ficha"], {"saludo": "hola", "prioridad": "alta"})
        self.assertEqual(r["solicitud"], {"prioridad": "alta"})
        self.assertEqual(r["respuesta"], "hola!")
        e = self.modelo.llamadas[0]["esquema"]
        self.assertEqual((e["type"], e["properties"]["saludo"]["type"]), ("object", "string"))
        self.assertTrue(self.modelo.llamadas[1]["json"])
        self.assertIsNone(self.modelo.llamadas[1]["esquema"])

    def test_resumir_y_redactar(self):
        d = cadena(nodo("r", "resumir", entradas=["correo"], longitud="3 viñetas", enfoque="los plazos"),
                   nodo("red", "ia_redactar", instrucciones="Responde con base en {{resumen}}", variable="carta"))
        self.assertEqual(self.problemas(d), [])
        r, _ = self.correr(d)
        self.assertTrue(r["variables"]["resumen"].startswith("TEXTO GENERADO"))
        self.assertTrue(r["variables"]["carta"].startswith("TEXTO GENERADO"))
        self.assertIn("3 viñetas", self.modelo.llamadas[0]["sistema"])
        self.assertIn("los plazos", self.modelo.llamadas[0]["sistema"])
        self.assertIn("Responde con base en TEXTO GENERADO", self.modelo.llamadas[1]["sistema"])

    def test_enviar_por_defecto_al_remitente(self):
        d = cadena(nodo("g", "ia_generar", instrucciones="Redacta", salida="respuesta"), nodo("e", "enviar", cuerpo="{{respuesta}}\n\nAtentamente"),
                   fin=nodo("fin", "salida", desenlace="responder", plantilla_respuesta="{{e.cuerpo}}"))
        self.assertEqual(self.problemas(d), [])
        r, ev = self.correr(d)
        m = r["mensajes"][0]
        self.assertEqual((m["para"], m["asunto"], m["operacion"]), ("rrhh@empresa.example", "RE: Solicitud pago incapacidad", "enviar"))
        self.assertTrue(r["respuesta"].endswith("Atentamente"))
        esc = [e for e in ev if e["tipo"] == "escritura"]
        self.assertEqual(len(esc), 1)
        self.assertTrue(esc[0]["bloqueada"])

    def test_flujo_sin_ia_ni_ocr(self):
        d = cadena(nodo("rx", "extraer_regex", de="correo.cuerpo", campos=[{"nombre": "cedula", "patron": r"c[eé]dula ([\d.]+)"}]),
                   nodo("t", "transformar", op="solo_digitos", de="cedula", salida="cedula"),
                   fin=nodo("fin", "salida", desenlace="radicar", campos=["cedula"]))
        d["nodos"][0]["config"] = {"leer_adjuntos": False}
        r, ev = self.correr(d)
        self.assertEqual(r["solicitud"], {"cedula": "1023456789"})
        self.assertEqual(self.modelo.llamadas, [])
        self.assertEqual(r["tokens_entrada"], 0)

    def test_flujo_invalido_no_corre(self):
        self.salida.seek(0)
        asyncio.run(fd.procesar({"cmd": "procesar", "corrida": "x", "caso": CASO, "definicion": cadena(nodo("g", "ia_generar"))}))
        r = [json.loads(x[len(fd.MARCA):]) for x in self.salida.getvalue().split(SALTO) if x.startswith(fd.MARCA)][-1]
        self.assertEqual(r["accion"], "error")
        self.assertTrue(any("instrucciones" in a for a in r["avisos"]))


# ------------------------------------------------------------- fallos

class Error429(Exception):
    """Como los errores de la API: trae su código HTTP."""

    def __init__(self, code=429):
        super().__init__(f"{code} RESOURCE_EXHAUSTED")
        self.code = code


def con_rama_de_error(n, **fin_error):
    """correo → n → ok; n --error--> avisar → manual."""
    return flujo([nodo("correo", "entrada_correo", leer_adjuntos=False), n,
                  nodo("avisar", "notificar", para="soporte@x", asunto="Falló {{fallo.nodo}}", cuerpo="{{fallo.motivo}} ({{fallo.tipo}})"),
                  nodo("ok", "salida", desenlace="radicar"), nodo("mal", "salida", desenlace="manual", motivo="{{fallo.motivo}}", **fin_error)],
                 [{"desde": "correo", "hacia": n["id"]}, {"desde": n["id"], "hacia": "ok"}, {"desde": n["id"], "hacia": "avisar", "cuando": "error"},
                  {"desde": "avisar", "hacia": "mal"}])


class Fallos(Base):
    def test_rama_de_error(self):
        d = con_rama_de_error(nodo("g", "ia_generar", instrucciones="x", salida="r"))
        self.assertEqual(self.problemas(d), [])
        self.modelo.fallos = [ValueError("respuesta imposible")]
        r, ev = self.correr(d)
        self.assertEqual(r["accion"], "manual")
        self.assertIn("respuesta imposible", r["motivo"])
        self.assertEqual(r["variables"]["fallo"]["nodo"], "g")
        self.assertEqual(r["variables"]["fallo"]["tipo"], "ia_generar")
        self.assertEqual(r["mensajes"][0]["asunto"], "Falló g")
        self.assertIn("(ia_generar)", r["mensajes"][0]["cuerpo"])
        fin = next(e for e in ev if e["tipo"] == "etapa_fin" and e["etapa"] == "g")
        self.assertTrue(fin["rama_error"] and fin["fallo"])
        self.assertEqual((fin["salida"], fin["siguiente"]), ("error", "avisar"))
        self.assertTrue(any(e["tipo"] == "paso" and e["nodo"] == "g" and e["tono"] == "error" and e.get("rama") == "error" for e in ev))
        self.assertTrue(any("rama de error" in a for a in r["avisos"]))
        # sin fallo, la rama de error no se toma aunque la arista por defecto exista
        r, _ = self.correr(d)
        self.assertEqual(r["accion"], "radicado")
        self.assertNotIn("fallo", r["variables"])

    def test_continuar_si_falla(self):
        d = cadena(nodo("g", "ia_generar", instrucciones="x", salida="r", continuar_si_falla=True),
                   fin=nodo("fin", "salida", desenlace="manual", motivo="{{fallo.motivo}}"))
        self.assertEqual(self.problemas(d), [])
        self.modelo.fallos = [Error429(503)]
        r, ev = self.correr(d)
        self.assertEqual(r["accion"], "manual")
        self.assertIn("503", r["motivo"])
        self.assertTrue(any("continuó" in a for a in r["avisos"]))
        fin = next(e for e in ev if e["tipo"] == "etapa_fin" and e["etapa"] == "g")
        self.assertTrue(fin["continua"])
        self.assertEqual(fin["siguiente"], "fin")

    def test_sin_rama_ni_continuar_aborta_como_antes(self):
        self.modelo.fallos = [ValueError("roto")]
        r, ev = self.correr_crudo(cadena(nodo("g", "ia_generar", instrucciones="x")))
        self.assertEqual(r["accion"], "error")
        self.assertIn("roto", r["motivo"])
        fin = next(e for e in ev if e["tipo"] == "etapa_fin" and e["etapa"] == "g")
        self.assertIn("roto", fin["fallo"])

    def test_validacion_de_la_rama_de_error(self):
        for t in fd.TIPOS - fd.TERMINALES:
            self.assertIn("error", fd.salidas_posibles({"tipo": t, "config": {}})[1], t)
        self.assertNotIn("error", fd.salidas_posibles({"tipo": "salida", "config": {}})[1])
        # {{fallo.*}} solo existe tras la rama de error
        self.assertProblema(cadena(nodo("g", "ia_generar", instrucciones="{{fallo.motivo}}")), "«fallo» solo existe")
        d = con_rama_de_error(nodo("g", "ia_generar", instrucciones="x"))
        d["nodos"][2]["config"]["cuerpo"] = "{{fallo.telefono}}"
        self.assertProblema(d, "«fallo.telefono» no existe")
        # lo que el nodo iba a producir no llega por la rama de error
        d = con_rama_de_error(nodo("g", "ia_generar", instrucciones="x", salida="r"))
        d["nodos"][2]["config"]["cuerpo"] = "{{r}}"
        self.assertProblema(d, "«r»")
        # «fallo» es reservada y «error» no puede ser categoría
        self.assertProblema(cadena(nodo("g", "ia_generar", instrucciones="x", salida="fallo")), "no es un nombre de variable válido")
        d = cadena(nodo("c", "ia_clasificar", categorias=["error", "otro"], variable="tema"))
        self.assertProblema(d, "rama de fallo")
        self.assertEqual(fd.validar(d, al_ejecutar=True), [])
        self.assertProblema(cadena(fin=nodo("fin", "salida", desenlace="manual", continuar_si_falla=True)), "no puede continuar")
        self.assertProblema(cadena(nodo("g", "ia_generar", instrucciones="x", continuar_si_falla="sí")), "continuar_si_falla tiene que ser")

    def test_describir_fallo(self):
        n = {"id": "g", "tipo": "ia_generar"}
        self.assertIn("429", fd.describir_fallo(n, Error429()))
        self.assertIn("a tiempo", fd.describir_fallo(n, TimeoutError()))
        self.assertIn("conectarse", fd.describir_fallo({"id": "h", "tipo": "http"}, ConnectionRefusedError()))
        self.assertEqual(fd.describir_fallo(n, fd.ErrorDeNegocio("falta el documento")), "falta el documento")


class Reintentos(Base):
    def test_reintenta_lo_pasajero(self):
        d = cadena(nodo("g", "ia_generar", instrucciones="x", salida="r", reintentos={"intentos": 3, "espera_ms": 100, "backoff": 2, "maximo_ms": 150}))
        self.assertEqual(self.problemas(d), [])
        self.modelo.fallos = [Error429(), TimeoutError("lento")]
        r, ev = self.correr(d)
        self.assertTrue(r["variables"]["r"].startswith("TEXTO GENERADO"))
        self.assertEqual(len(self.modelo.llamadas), 3)
        pasos = [e for e in ev if e["tipo"] == "paso" and e.get("reintento")]
        self.assertEqual([p["reintento"] for p in pasos], [1, 2])
        self.assertEqual(pasos[0]["titulo"], "Reintento 1 de 2")
        self.assertEqual(len(self.esperas), 2)
        self.assertTrue(0 <= self.esperas[0] <= 0.1 and 0 <= self.esperas[1] <= 0.15, self.esperas)  # full jitter con tope

    def test_no_reintenta_errores_de_negocio_y_se_agota(self):
        d = cadena(nodo("g", "ia_generar", instrucciones="x", reintentos={"intentos": 5}))
        self.modelo.fallos = [ValueError("dato malo")]
        r, _ = self.correr_crudo(d)
        self.assertEqual((r["accion"], len(self.modelo.llamadas)), ("error", 1))
        self.modelo.llamadas.clear()
        self.modelo.fallos = [Error429(500)] * 2
        d["nodos"][1]["config"]["reintentos"] = {"intentos": 2, "espera_ms": 0}
        r, _ = self.correr_crudo(d)
        self.assertEqual((r["accion"], len(self.modelo.llamadas)), ("error", 2))

    def test_clasificacion_y_espera(self):
        self.assertTrue(fd.es_transitorio(Error429()))
        self.assertTrue(fd.es_transitorio(Error429(502)))
        self.assertTrue(fd.es_transitorio(ConnectionResetError()))
        self.assertTrue(fd.es_transitorio(TimeoutError()))
        self.assertFalse(fd.es_transitorio(Error429(400)))
        self.assertFalse(fd.es_transitorio(ValueError("x")))
        self.assertFalse(fd.es_transitorio(fd.ErrorDeNegocio("x")))
        for k in range(1, 6):
            self.assertTrue(0 <= fd.espera_con_jitter(k, 500, 2, 3000) <= min(3000, 500 * 2 ** (k - 1)))

    def test_validacion(self):
        self.assertProblema(cadena(nodo("g", "ia_generar", instrucciones="x", reintentos={"intentos": 9})), "entre 1 y 5")
        self.assertProblema(cadena(nodo("g", "ia_generar", instrucciones="x", reintentos={"intentos": 2, "backoff": 0})), "backoff")
        self.assertProblema(cadena(nodo("g", "ia_generar", instrucciones="x", reintentos=3)), "tiene que ser un objeto")
        self.assertProblema(cadena(nodo("f", "filtro", campo="asunto", valor="x", reintentos={"intentos": 2})), "solo aplica")
        self.assertEqual(fd._validar_config(nodo("e", "enviar", cuerpo="x", reintentos={"intentos": 2})), [])


class Deshabilitado(Base):
    def test_se_omite_y_sigue(self):
        d = cadena(nodo("g", "ia_generar", instrucciones="x", salida="r", deshabilitado=True),
                   nodo("t", "transformar", op="plantilla", plantilla="[{{r}}]", salida="t"))
        self.assertEqual(self.problemas(d), [])
        r, ev = self.correr(d)
        self.assertEqual(self.modelo.llamadas, [])
        self.assertEqual(r["variables"]["t"], "[]")
        fin = next(e for e in ev if e["tipo"] == "etapa_fin" and e["etapa"] == "g")
        self.assertTrue(fin["omitido"])
        self.assertEqual(fin["siguiente"], "t")
        self.assertTrue(any(e["tipo"] == "paso" and e.get("omitido") and "se omite" in e["titulo"] for e in ev))

    def test_sin_arista_por_defecto_toma_la_primera(self):
        d = flujo([nodo("correo", "entrada_correo", leer_adjuntos=False), nodo("c", "ia_clasificar", categorias=["a", "b"], variable="v", deshabilitado=True),
                   nodo("sa", "salida", desenlace="radicar"), nodo("sb", "salida", desenlace="manual"), nodo("se", "salida", desenlace="descartar")],
                  [{"desde": "correo", "hacia": "c"}, {"desde": "c", "hacia": "se", "cuando": "error"}, {"desde": "c", "hacia": "sa", "cuando": "a"},
                   {"desde": "c", "hacia": "sb", "cuando": "b"}])
        self.assertEqual(self.problemas(d), [])
        self.assertEqual(self.correr(d)[0]["accion"], "radicado")  # la primera que no es de error

    def test_validacion(self):
        d = cadena()
        d["nodos"][0]["config"]["deshabilitado"] = True
        self.assertProblema(d, "no se puede deshabilitar")
        self.assertProblema(cadena(nodo("g", "ia_generar", instrucciones="x", deshabilitado="no")), "deshabilitado tiene que ser")


# ------------------------------------------------------------- plantillas nuevas

class PlantillasAvanzadas(Base):
    def test_selectores_indices_y_claves_con_punto(self):
        e = fd.Ejecucion(cadena(), CASO, "m")
        e.entrada_correo(nodo("correo", "entrada_correo", leer_adjuntos=False))
        e.vars.update({"polizas": [{"id": "P1", "estado": "vencida"}, {"id": "P2", "estado": "Activa", "plan": {"nombre": "Oro"}}],
                       "datos": {"a.b": "punto", "con espacio": "sí", "0": "cero"}, "nums": [10, 20]})
        self.assertEqual(e.render("{{polizas[estado=activa].id}}"), "P2")
        self.assertEqual(e.render("{{polizas[estado=\"vencida\"].id}}|{{polizas[estado='activa'].plan.nombre}}"), "P1|Oro")
        self.assertEqual(e.render("{{polizas.0.id}}{{polizas[1].id}}{{nums.1}}"), "P1P220")
        self.assertEqual(e.render("{{datos[\"a.b\"]}}/{{datos['con espacio']}}/{{datos.0}}"), "punto/sí/cero")
        self.assertEqual(e.render("[{{polizas[estado=nada].id}}][{{polizas.9.id}}][{{nums.x}}]"), "[][][]")
        self.assertEqual(e.render("{{correo.asunto}}"), "Solicitud pago incapacidad")

    def test_segmentos(self):
        self.assertEqual(fd.segmentos('a.b[0][c="x y"]["k.l"].2'),
                         [("clave", "a"), ("clave", "b"), ("indice", 0), ("filtro", "c", "x y"), ("clave", "k.l"), ("clave", "2")])
        for malo in ("", "a..b", "a[", "a[b=c", "a.b]", "a b"):
            self.assertIsNone(fd.segmentos(malo), malo)

    def test_validacion_entiende_las_rutas(self):
        base = nodo("x", "ia_extraer", salida="datos", campos=[{"nombre": "lista"}])
        d = cadena(base, nodo("g", "ia_generar", instrucciones="{{lista[estado=activa].id}} {{datos[\"a.b\"]}} {{lista.0.id}} {{correo.asunto}}"))
        self.assertEqual(self.problemas(d), [])
        self.assertProblema(cadena(base, nodo("g", "ia_generar", instrucciones="{{lista[estado=}}")), "no es válida")
        self.assertProblema(cadena(base, nodo("g", "ia_generar", instrucciones="{{nada[0].id}}")), "«nada»")
        self.assertProblema(cadena(nodo("g", "ia_generar", instrucciones="{{correo[0]}}")), "no existe")


# ------------------------------------------------------------- http

SECRETO = "tok-SECRETO-123"


class _Servidor(http.server.BaseHTTPRequestHandler):
    vistas = []
    contador = {}

    def log_message(self, *a):
        pass

    def _responder(self, status, cuerpo=None, **cabeceras):
        datos = json.dumps(cuerpo).encode() if cuerpo is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(datos)))
        for k, v in cabeceras.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(datos)

    def do_GET(self):
        ruta = self.path.split("?")[0]
        _Servidor.vistas.append({"ruta": self.path, "auth": self.headers.get("Authorization")})
        _Servidor.contador[ruta] = _Servidor.contador.get(ruta, 0) + 1
        if ruta == "/afiliados":
            self._responder(200, {"estado": "activo", "empresa": {"nombre": "ACME"},
                                  "polizas": [{"id": "P1", "estado": "vencida"}, {"id": "P2", "estado": "activa"}]})
        elif ruta == "/falla-una":
            self._responder(500, {"x": 1}) if _Servidor.contador[ruta] == 1 else self._responder(200, {"estado": "repuesto"})
        elif ruta == "/siempre-503":
            self._responder(503, {"x": 1})
        elif ruta == "/lento":
            time.sleep(1.0)
            self._responder(200, {})
        elif ruta == "/redirige":
            self._responder(302, None, Location="http://169.254.169.254/latest/meta-data/")
        else:
            self._responder(404, {"error": "no existe"})

    do_POST = do_GET


class Http(Base):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Servidor)
        cls.srv.handle_error = lambda *a: None  # el cliente que cortó por tiempo deja un «broken pipe» sin importancia
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        super().setUp()
        _Servidor.vistas.clear()
        _Servidor.contador.clear()
        os.environ["HTTP_TOKEN_PRUEBA"] = SECRETO

    def tearDown(self):
        os.environ.pop("HTTP_TOKEN_PRUEBA", None)
        super().tearDown()

    def permitir_local(self):
        """Solo en estas pruebas: el host local permitido y su loopback fuera del bloqueo SSRF."""
        os.environ["HTTP_HOSTS_PERMITIDOS"] = "127.0.0.1"
        fd._IPS_PERMITIDAS_PRUEBAS.add("127.0.0.1")

    def flujo_http(self, ruta="/afiliados", **conf):
        conf = {"url": self.url + ruta, "timeout_ms": 3000, "salida": "resp", **conf}
        return flujo([nodo("correo", "entrada_correo", leer_adjuntos=False), nodo("h", "http", **conf),
                      nodo("ok", "salida", desenlace="radicar"), nodo("err_http", "salida", desenlace="manual", motivo="HTTP {{resp.status}}"),
                      nodo("err", "salida", desenlace="descartar", motivo="{{fallo.motivo}}")],
                     [{"desde": "correo", "hacia": "h"}, {"desde": "h", "hacia": "ok", "cuando": "ok"},
                      {"desde": "h", "hacia": "err_http", "cuando": "error_http"}, {"desde": "h", "hacia": "err", "cuando": "error"}])

    def test_get_permitido_con_mapeo_credencial_y_query(self):
        self.permitir_local()
        d = self.flujo_http(query={"doc": "{{correo.remitente_correo}}"}, credencial="HTTP_TOKEN_PRUEBA", mapeo=[
            {"variable": "estado", "ruta": "estado"}, {"variable": "poliza", "ruta": "polizas[estado=activa].id", "obligatorio": True},
            {"variable": "empresa", "ruta": "empresa.nombre"}, {"variable": "plan", "ruta": "plan.nombre", "defecto": "sin plan"}])
        self.assertEqual(self.problemas(d), [])
        r, ev = self.correr(d)
        self.assertEqual(r["accion"], "radicado")
        v = r["variables"]
        self.assertEqual((v["estado"], v["poliza"], v["empresa"], v["plan"]), ("activo", "P2", "ACME", "sin plan"))
        self.assertEqual(v["resp"]["status"], 200)
        self.assertEqual(_Servidor.vistas[0]["auth"], f"Bearer {SECRETO}")
        self.assertIn("doc=rrhh%40empresa.example", _Servidor.vistas[0]["ruta"])
        self.assertNotIn(SECRETO, self.salida.getvalue())  # el secreto nunca sale en los eventos
        self.assertTrue(any(e["tipo"] == "busqueda" and e["metodo"] == "http" and e["campo"] == "poliza" for e in ev))
        fin = next(e for e in ev if e["tipo"] == "etapa_fin" and e["etapa"] == "h")
        self.assertEqual(fin["muestra"]["poliza"], "P2")

    def test_escrituras_y_hosts_no_permitidos_se_simulan(self):
        self.permitir_local()
        r, ev = self.correr(self.flujo_http(metodo="POST", cuerpo={"doc": "{{correo.remitente_correo}}"}, credencial="HTTP_TOKEN_PRUEBA"))
        self.assertEqual(r["accion"], "radicado")
        self.assertEqual(_Servidor.vistas, [])  # no salió nada
        self.assertTrue(r["variables"]["resp"]["simulado"])
        self.assertEqual(r["variables"]["resp"]["cuerpo"], {"doc": "rrhh@empresa.example"})
        esc = next(e for e in ev if e["tipo"] == "escritura")
        self.assertTrue(esc["bloqueada"] and esc["simulada"])
        self.assertEqual((esc["sistema"], esc["operacion"]), ("http", "POST"))
        self.assertEqual(esc["detalle"]["encabezados"]["Authorization"], "Bearer ‹HTTP_TOKEN_PRUEBA›")
        self.assertEqual(r["peticiones"][0]["metodo"], "POST")
        self.assertNotIn(SECRETO, self.salida.getvalue())
        # GET sin lista de hosts (o con otro host): simulado también
        os.environ["HTTP_HOSTS_PERMITIDOS"] = ""
        r, _ = self.correr(self.flujo_http())
        self.assertIn("vacío", r["variables"]["resp"]["motivo"])
        os.environ["HTTP_HOSTS_PERMITIDOS"] = "api.ejemplo.gov.co"
        r, _ = self.correr(self.flujo_http())
        self.assertIn("no está en HTTP_HOSTS_PERMITIDOS", r["variables"]["resp"]["motivo"])
        self.assertEqual(_Servidor.vistas, [])

    def test_bloqueo_ssrf(self):
        os.environ["HTTP_HOSTS_PERMITIDOS"] = "127.0.0.1,localhost,metadata.google.internal"  # aunque estén permitidos
        r, _ = self.correr(self.flujo_http())
        self.assertEqual(r["accion"], "descartado")
        self.assertIn("bloqueado por seguridad", r["motivo"])
        self.assertEqual(_Servidor.vistas, [])
        for ip in ("127.0.0.1", "10.1.2.3", "192.168.0.10", "172.16.5.4", "169.254.169.254", "::1", "fe80::1", "::ffff:10.0.0.1", "0.0.0.0", "100.64.0.1"):
            self.assertIsNotNone(fd.ip_bloqueada(ip), ip)
        self.assertIsNone(fd.ip_bloqueada("8.8.8.8"))
        for host in ("metadata.google.internal", "localhost", "algo.internal"):
            with self.assertRaises(fd.ErrorDeNegocio):
                fd.destino_seguro(host, 80)

    def test_redireccion_no_se_sigue(self):
        self.permitir_local()
        r, _ = self.correr(self.flujo_http("/redirige"))
        self.assertEqual(r["variables"]["resp"]["status"], 302)
        self.assertEqual(_Servidor.contador, {"/redirige": 1})
        self.assertIn("169.254.169.254", r["variables"]["resp"]["redireccion"])

    def test_error_http_reintentos_y_tiempo(self):
        self.permitir_local()
        r, _ = self.correr(self.flujo_http("/no-existe"))
        self.assertEqual((r["accion"], r["motivo"]), ("manual", "HTTP 404"))
        # un 500 pasajero se reintenta
        r, ev = self.correr(self.flujo_http("/falla-una", reintentos={"intentos": 3, "espera_ms": 0}))
        self.assertEqual(r["accion"], "radicado")
        self.assertEqual(_Servidor.contador["/falla-una"], 2)
        self.assertEqual(len([e for e in ev if e["tipo"] == "paso" and e.get("reintento")]), 1)
        # un 503 que no cede: se agotan los intentos y sale por error_http
        r, _ = self.correr(self.flujo_http("/siempre-503", reintentos={"intentos": 2, "espera_ms": 0}))
        self.assertEqual((r["accion"], r["motivo"], _Servidor.contador["/siempre-503"]), ("manual", "HTTP 503", 2))
        # sin reintentos, un 404 no se repite
        self.assertEqual(_Servidor.contador["/no-existe"], 1)
        # tiempo agotado → rama de error
        r, _ = self.correr(self.flujo_http("/lento", timeout_ms=200))
        self.assertEqual(r["accion"], "descartado")
        self.assertIn("a tiempo", r["motivo"])
        # obligatorio que no viene → rama de error
        r, _ = self.correr(self.flujo_http(mapeo=[{"variable": "x", "ruta": "no.esta", "obligatorio": True}]))
        self.assertEqual(r["accion"], "descartado")
        self.assertIn("no trae x", r["motivo"])

    def test_validacion(self):
        def p(**conf):
            return fd._validar_config(nodo("h", "http", **conf))
        self.assertEqual(p(**nd.TIPOS["http"]["ejemplo"]["config"]), [])
        casos = [({}, "necesita url"), ({"url": "ftp://x"}, "http:// o https://"), ({"url": "https://x", "metodo": "TRACE"}, "metodo"),
                 ({"url": "https://x", "encabezados": {"Authorization": "Bearer abc"}}, "usa credencial"),
                 ({"url": "https://x", "credencial": "Bearer abc123"}, "NOMBRE de una"),
                 ({"url": "https://x", "timeout_ms": 120000}, "timeout_ms"), ({"url": "https://x", "cuerpo": 5}, "cuerpo"),
                 ({"url": "https://x", "mapeo": [{"variable": "a", "ruta": "x["}]}, "ruta"),
                 ({"url": "https://x", "mapeo": [{"variable": "correo", "ruta": "x"}]}, "variable «correo»"),
                 ({"url": "https://x", "query": {"a": 1}}, "query")]
        for conf, texto in casos:
            with self.subTest(conf=conf):
                self.assertTrue(any(texto in x for x in p(**conf)), p(**conf))
        d = cadena(nodo("h", "http", url="https://api.x/{{nada}}", mapeo=[{"variable": "a", "ruta": "a"}]),
                   nodo("g", "ia_generar", instrucciones="{{a}} {{h.status}}"))
        self.assertProblema(d, "«nada»")
        self.assertEqual(fd.salidas_posibles(d["nodos"][1]), (["ok", "error_http"], ["error"]))


# ------------------------------------------------------------- muestra

class Muestra(Base):
    def test_muestra_acotada_por_nodo(self):
        d = cadena(nodo("t", "transformar", operaciones=[
            {"op": "asignar", "valor": "x" * 1000, "salida": "largo"},
            {"op": "asignar", "valor": list(range(50)), "salida": "lista"},
            {"op": "asignar", "valor": {"a": {"b": {"c": {"d": {"e": "hondo"}}}}}, "salida": "hondo"}]))
        r, ev = self.correr(d)
        fin = next(e for e in ev if e["tipo"] == "etapa_fin" and e["etapa"] == "t")
        m = fin["muestra"]
        self.assertEqual(set(m), {"largo", "lista", "hondo"})
        self.assertEqual(len(m["largo"]), 301)
        self.assertEqual(len(m["lista"]), 21)
        self.assertEqual(m["hondo"]["a"]["b"]["c"], {"d": "…"})  # cuatro niveles y se corta
        correo = next(e for e in ev if e["tipo"] == "etapa_fin" and e["etapa"] == "correo")
        self.assertEqual(correo["muestra"]["correo"]["asunto"], "Solicitud pago incapacidad")
        self.assertIn("muestra", next(e for e in ev if e["tipo"] == "etapa_fin" and e["etapa"] == "fin"))


# ------------------------------------------------------------- documentación

class Documentacion(unittest.TestCase):
    def test_mismos_tipos(self):
        self.assertEqual(set(nd.TIPOS), fd.TIPOS)
        self.assertEqual(set(nd.DESENLACES), set(fd.DESENLACES))

    def test_documentacion_y_esquema(self):
        for t in nd.TIPOS:
            self.assertIn(f"- {t}:", nd.DOCUMENTACION)
        e = json.loads(json.dumps(nd.esquema(), ensure_ascii=False))
        self.assertEqual(set(e["tipos"]), fd.TIPOS)
        for t, spec in e["tipos"].items():
            self.assertTrue({"nombre", "categoria", "descripcion", "config", "salidas", "ejemplo"} <= set(spec), t)
        for t in fd.TIPOS - fd.TERMINALES:  # lo que la doc dice que sale coincide con el motor
            oblig, _ = fd.salidas_posibles(nd.TIPOS[t]["ejemplo"])
            self.assertTrue(oblig, t)
        self.assertEqual(e["version"], nd.VERSION)
        for clave in ("deshabilitado", "continuar_si_falla", "reintentos"):
            self.assertIn(clave, e["comunes"])
            self.assertIn(clave, nd.DOCUMENTACION)
        for texto in ("- http:", "HTTP_HOSTS_PERMITIDOS", "\"error\"", "{{fallo.motivo}}", "[campo=valor]"):
            self.assertIn(texto, nd.DOCUMENTACION)

    def test_ayuda_por_tipo(self):
        e = nd.esquema()
        for t, spec in e["tipos"].items():
            with self.subTest(tipo=t):
                self.assertTrue(1 <= len(spec["ejemplos"]) <= 2, t)
                self.assertTrue(1 <= len(spec["consejos"]) <= 3, t)
                self.assertTrue(all(isinstance(x, str) and x.strip() for x in spec["consejos"]))
                for conf in spec["ejemplos"]:  # cada ejemplo se puede copiar tal cual
                    self.assertEqual(fd._validar_config({"id": "n", "tipo": t, "config": conf}), [], conf)

    def test_contrato_de_tipos(self):
        """GET /api/nodos/tipos: la forma que esperan el editor y Vera, con JSON Schema del subconjunto acordado."""
        formatos = {"plantilla", "credencial", "variable", "expresion", "texto-largo", "cron"}
        claves = {"type", "properties", "required", "enum", "items", "description", "default", "minimum", "maximum", "title", "format",
                  "x-credencial-tipos", "x-comun", "x-largo", "additionalProperties", "minItems"}

        def revisar(esq, donde):
            self.assertTrue(set(esq) <= claves, f"{donde}: {set(esq) - claves}")
            if "format" in esq:
                self.assertIn(esq["format"], formatos, donde)
            if esq.get("format") == "credencial":
                self.assertTrue(set(esq["x-credencial-tipos"]) <= set(nd.TIPOS_CREDENCIAL), donde)
            for k, v in (esq.get("properties") or {}).items():
                revisar(v, f"{donde}.{k}")
            if isinstance(esq.get("items"), dict):
                revisar(esq["items"], f"{donde}[]")
            if isinstance(esq.get("additionalProperties"), dict):
                revisar(esq["additionalProperties"], f"{donde}{{}}")

        tipos = json.loads(json.dumps(nd.tipos_contrato(), ensure_ascii=False))
        self.assertEqual({t["tipo"] for t in tipos}, fd.TIPOS)
        for t in tipos:
            with self.subTest(tipo=t["tipo"]):
                self.assertTrue({"tipo", "nombre", "categoria", "descripcion", "disparador", "escritura", "salidas", "config", "ejemplos", "consejos"} <= set(t))
                self.assertEqual(t["config"]["type"], "object")
                revisar(t["config"], t["tipo"])
                self.assertEqual(t["disparador"], t["tipo"] in fd.DISPARADORES)
                self.assertEqual(t["escritura"], t["tipo"] in fd.TIPOS_ESCRITURA)
                self.assertTrue(t["salidas"] == "dinamicas" or isinstance(t["salidas"], list))
                for k in t["config"].get("required") or []:
                    self.assertIn(k, t["config"]["properties"])
        dinamicas = {t["tipo"] for t in tipos if t["salidas"] == "dinamicas"}
        self.assertEqual(dinamicas, {"ia_clasificar", "ia_decidir", "bifurcar_por_valor", "paralelo"})
        r = subprocess.run([sys.executable, str(AQUI / "nodos_declarativos.py"), "tipos"], capture_output=True, timeout=60)
        self.assertEqual(len(json.loads(r.stdout.decode("utf-8"))), len(fd.TIPOS))


# =====================================================================
#   Plataforma de automatización: disparadores, conectores, bucles,
#   expresiones, credenciales, entorno por flujo y límites.
# =====================================================================

def manual(*nodos, campos=None, aristas=None, fin=None):
    """Un flujo con entrada_manual; lineal salvo que se den las aristas."""
    fin = fin or nodo("fin", "salida", desenlace="manual")
    ns = [nodo("inicio", "entrada_manual", campos=campos or [])] + list(nodos) + [fin]
    return flujo(ns, aristas if aristas is not None else [{"desde": a["id"], "hacia": b["id"]} for a, b in zip(ns, ns[1:])])


def cred(tipo, permitir=False, secretos=(), **valores):
    return {"tipo": tipo, "valores": valores, "secretos": list(secretos), "permitir_escritura": permitir}


CLAVE_SQL = "clave-sql-SUPERSECRETA-987"
TOKEN = "tok-API-ULTRA-SECRETO-555"


class Plataforma(Base):
    def setUp(self):
        super().setUp()
        raiz = Path(self.tmp.name)
        self.archivos = raiz / "archivos"
        self.estado = raiz / "estado"
        self.archivos.mkdir()
        self.estado.mkdir()
        self._env = {k: os.environ.get(k) for k in ("RUTA_ARCHIVOS", "RUTA_DATOS")}
        os.environ["RUTA_ARCHIVOS"], os.environ["RUTA_DATOS"] = str(self.archivos), str(self.estado)
        self._recibidos = fd.RECIBIDOS
        fd.RECIBIDOS = self.estado / "recibidos"
        import sqlite3
        con = sqlite3.connect(str(self.archivos / "base.db"))
        con.execute("CREATE TABLE casos (id INTEGER PRIMARY KEY, documento TEXT, monto REAL, estado TEXT)")
        con.executemany("INSERT INTO casos (documento, monto, estado) VALUES (?, ?, ?)",
                        [("111", 100.0, "pendiente"), ("222", 250.0, "pendiente"), ("333", 80.0, "cerrado")])
        con.commit()
        con.close()
        self.creds = {"base": cred("sqlite", ruta="base.db"), "base-escribe": cred("sqlite", True, ruta="base.db")}

    def tearDown(self):
        fd.RECIBIDOS = self._recibidos
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        super().tearDown()

    def correr_orden(self, defi, exigir_ok=True, **orden):
        self.salida.seek(0)
        self.salida.truncate()
        orden.setdefault("credenciales", self.creds)
        asyncio.run(fd.procesar({"cmd": "procesar", "corrida": "x", "caso": orden.pop("caso", "manual-prueba"), "definicion": defi, **orden}))
        eventos = [json.loads(linea[len(fd.MARCA):]) for linea in self.salida.getvalue().split(SALTO) if linea.startswith(fd.MARCA)]
        r = next(e for e in eventos if e["tipo"] == "resultado")
        if exigir_ok:
            self.assertNotEqual(r["accion"], "error", f"{r.get('motivo')}\n{r.get('avisos')}\n{r.get('traza', '')}")
        return r, eventos

    def filas_bd(self, sql="SELECT estado FROM casos ORDER BY id"):
        import sqlite3
        con = sqlite3.connect(str(self.archivos / "base.db"))
        try:
            return [f[0] for f in con.execute(sql)]
        finally:
            con.close()


class Disparadores(Plataforma):
    def test_exactamente_un_disparador(self):
        d = manual()
        d["nodos"].append(nodo("otro", "programado", cada_minutos=5))
        self.assertProblema(d, "exactamente un disparador")
        self.assertProblema(flujo([nodo("fin", "salida", desenlace="manual")], []), "hay 0")

    def test_entrada_manual_revisa_y_convierte(self):
        campos = [{"nombre": "documento", "tipo": "texto", "obligatorio": True}, {"nombre": "monto", "tipo": "numero"},
                  {"nombre": "urgente", "tipo": "booleano"}, {"nombre": "items", "tipo": "lista"}]
        d = manual(nodo("t", "expresion", expresion="entrada.monto * 2 if entrada.urgente else 0", salida="doble"), campos=campos)
        self.assertEqual(self.problemas(d), [])
        r, ev = self.correr_orden(d, entrada={"documento": "123", "monto": "1.234,5", "urgente": "sí", "items": "[1, 2]"})
        self.assertEqual(r["variables"]["entrada"], {"documento": "123", "monto": 1234.5, "urgente": True, "items": [1, 2]})
        self.assertEqual(r["variables"]["doble"], 2469.0)
        r, _ = self.correr_orden(d, exigir_ok=False, entrada={"monto": 3})
        self.assertEqual(r["accion"], "error")
        self.assertIn("faltan en la entrada: documento", r["motivo"])
        r, _ = self.correr_orden(d, exigir_ok=False, entrada={"documento": "1", "monto": "mucho"})
        self.assertIn("tiene que ser numero", r["motivo"])

    def test_programado_y_webhook(self):
        self.assertEqual(fd._validar_config(nodo("p", "programado", cron="0 7 * * 1-5", zona="America/Bogota", activo=True)), [])
        for conf, texto in [({}, "cron o cada_minutos"), ({"cron": "0 7 * *"}, "5 campos"), ({"cron": "61 * * * *"}, "fuera de"),
                            ({"cron": "* * * * *", "cada_minutos": 5}, "uno de los dos"), ({"cada_minutos": 0}, "entre 1"),
                            ({"cada_minutos": 5, "zona": "Marte/Olimpo"}, "zona"), ({"cada_minutos": 5, "activo": "si"}, "true o false")]:
            with self.subTest(conf=conf):
                p = fd._validar_config(nodo("p", "programado", **conf))
                self.assertTrue(any(texto in x for x in p), p)
        d = flujo([nodo("p", "programado", cada_minutos=1), nodo("fin", "salida", desenlace="manual", motivo="{{entrada.hora}}")],
                  [{"desde": "p", "hacia": "fin"}])
        r, _ = self.correr_orden(d, entrada={"programado": True, "hora": "2026-11-06T07:00:00-05:00"}, origen="programado")
        self.assertEqual(r["motivo"], "2026-11-06T07:00:00-05:00")
        w = flujo([nodo("w", "webhook", responder=True), nodo("j", "json", op="seleccionar", de="entrada.cuerpo", ruta="pedido.id", salida="id"),
                   nodo("resp", "respuesta_webhook", estado=201, cuerpo={"recibido": "{{id}}", "ok": True}), nodo("fin", "salida", desenlace="manual")],
                  [{"desde": "w", "hacia": "j"}, {"desde": "j", "hacia": "resp"}, {"desde": "resp", "hacia": "fin"}])
        self.assertEqual(self.problemas(w), [])
        r, ev = self.correr_orden(w, entrada={"cuerpo": {"pedido": {"id": 77}}, "consulta": {}, "encabezados": {}})
        self.assertEqual(r["respuesta_webhook"], {"estado": 201, "cuerpo": {"recibido": 77, "ok": True}, "tipo_contenido": "application/json"})
        self.assertTrue(any(e["tipo"] == "respuesta_webhook" and e["estado"] == 201 for e in ev))
        self.assertTrue(any("secreto" in x for x in fd._validar_config(nodo("w", "webhook", secreto="corto"))))

    def test_correo_nuevo_sondea_sin_repetir_y_corre_el_caso(self):
        imap = ImapFalso([correo_mime("1", "Factura 1", "uno@ejemplo.test"), correo_mime("2", "Factura 2", "dos@ejemplo.test", adjunto=("f.csv", b"a;b\n1;2\n"))])
        c_correo = fd.c_correo
        previo = c_correo.fabrica_imap
        c_correo.fabrica_imap = lambda *a: imap
        try:
            d = flujo([nodo("llega", "correo_nuevo", credencial="buzon", activo=True),
                       nodo("h", "hoja_leer", origen="adjunto", salida="filas"),
                       nodo("fin", "salida", desenlace="manual", motivo="{{correo.asunto}} · {{filas.0.b}}"),
                       nodo("sin", "salida", desenlace="descartar", motivo="{{correo.asunto}}")],
                      [{"desde": "llega", "hacia": "h"}, {"desde": "h", "hacia": "fin", "cuando": "ok"}, {"desde": "h", "hacia": "sin", "cuando": "vacio"},
                       {"desde": "h", "hacia": "sin", "cuando": "error"}])
            d["id"] = "facturas"
            self.assertEqual(self.problemas(d), [])
            orden = {"flujo": "facturas", "definicion": d, "credenciales": {"buzon": cred("imap", host="127.0.0.1", usuario="u@ejemplo.test", clave="x")}}
            primera = fd.sondear(orden)
            self.assertTrue(primera["ok"], primera)
            self.assertEqual((primera["casos"], primera["primera"]), ([], True))  # la primera vez solo memoriza
            imap.correos.append(correo_mime("3", "Factura 3", "tres@ejemplo.test", adjunto=("f.csv", b"a;b\n5;6\n")))
            segunda = fd.sondear(orden)
            self.assertEqual([c["asunto"] for c in segunda["casos"]], ["Factura 3"])
            self.assertEqual(fd.sondear(orden)["casos"], [])  # no se repite
            self.assertTrue(all(x[0] in ("login", "select", "search", "fetch", "logout") for x in imap.llamadas))
            self.assertTrue(all(x[1] is True for x in imap.llamadas if x[0] == "select"))  # EXAMINE: solo lectura
            self.assertTrue(all("PEEK" in x[1] for x in imap.llamadas if x[0] == "fetch"))  # no marca como leído
            r, _ = self.correr_orden(d, caso=segunda["casos"][0]["caso"], entrada={"id": "3"})
            self.assertEqual(r["motivo"], "Factura 3 · 6")
        finally:
            c_correo.fabrica_imap = previo


# ------------------------------------------------------------- dobles

def correo_mime(uid, asunto, de, adjunto=None):
    from email.message import EmailMessage
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Message-ID"] = f"Remitente {uid} <{de}>", "buzon@ejemplo.test", asunto, f"<m{uid}@ejemplo.test>"
    m["Date"] = "Fri, 06 Nov 2026 10:00:00 -0500"
    m.set_content(f"Cuerpo del correo {uid}")
    if adjunto:
        m.add_attachment(adjunto[1], maintype="text", subtype="csv", filename=adjunto[0])
    return (uid, m.as_bytes())


class ImapFalso:
    def __init__(self, correos):
        self.correos = list(correos)
        self.llamadas = []

    def login(self, u, c):
        self.llamadas.append(("login", u))

    def select(self, carpeta, readonly=False):
        self.llamadas.append(("select", readonly))
        return "OK", [b"3"]

    def uid(self, cmd, *args):
        if cmd == "SEARCH":
            self.llamadas.append(("search", args))
            return "OK", [" ".join(u for u, _ in self.correos).encode()]
        self.llamadas.append(("fetch", args[1]))
        crudo = next(c for u, c in self.correos if u == args[0])
        return "OK", [(b"1 (BODY[] {%d}" % len(crudo), crudo), b")"]

    def logout(self):
        self.llamadas.append(("logout", None))


class SmtpFalso:
    enviados = []

    def login(self, u, c):
        pass

    def send_message(self, m, from_addr=None, to_addrs=None):
        SmtpFalso.enviados.append({"de": from_addr, "para": to_addrs, "asunto": m["Subject"], "cuerpo": m.get_content()})

    def quit(self):
        pass


class DriveFalso:
    def __init__(self):
        self.subidos = []
        self.archivos = {"1AAAAAAAAAAAAA": {"id": "1AAAAAAAAAAAAA", "name": "notas.txt", "mimeType": "text/plain", "datos": b"hola desde drive"},
                         "1HOJAHOJAHOJA1": {"id": "1HOJAHOJAHOJA1", "name": "Planilla", "mimeType": "application/vnd.google-apps.spreadsheet",
                                            "datos": b"doc,monto\n111,10\n222,20\n"}}
        self.consultas = []

    def listar(self, q, maximo):
        self.consultas.append(q)
        return [{k: v for k, v in f.items() if k != "datos"} for f in self.archivos.values()][:maximo]

    def metadatos(self, id_):
        f = self.archivos.get(id_)
        if not f:
            raise fd.ErrorDeNegocio("Drive respondió 404: no existe")
        return {k: v for k, v in f.items() if k != "datos"}

    def exportar(self, id_, tipo):
        return self.archivos[id_]["datos"]

    def bajar(self, id_):
        return self.archivos[id_]["datos"]

    def subir(self, nombre, carpeta, datos, tipo):
        self.subidos.append((nombre, carpeta, datos, tipo))
        return {"id": "1NUEVONUEVONUE", "name": nombre}


# ------------------------------------------------------------- SQL

class Sql(Plataforma):
    def test_revisar_sentencias(self):
        r = fd.c_sql.revisar
        self.assertEqual(r("SELECT 1;", "lectura")[0], "SELECT 1")
        self.assertEqual(r("SELECT ';' AS x, 'a--b' AS y /* ; */", "lectura")[1], [])
        self.assertEqual(r("SELECT id::int FROM t WHERE d = :doc AND e = :doc", "lectura")[1], ["doc"])
        for sql, texto in [("SELECT 1; DROP TABLE casos", "UNA sentencia"), ("SELECT 1;\nSELECT 2", "UNA sentencia"),
                           ("DELETE FROM casos", "solo acepta SELECT"), ("WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x", "solo lectura"),
                           ("SELECT * INTO nueva FROM casos", "INTO"), ("SELECT pg_read_file('/etc/passwd')", "PG_READ_FILE"),
                           ("SELECT load_extension('x')", "LOAD_EXTENSION"), ("SELECT 1 /* sin cerrar", "sin cerrar"),
                           ("SELECT 'abierto", "sin cerrar"), ("PRAGMA writable_schema = 1", "solo acepta SELECT"),
                           ("SELECT nextval('s')", "NEXTVAL"), ("SELECT * FROM t FOR UPDATE", "UPDATE")]:
            with self.subTest(sql=sql):
                with self.assertRaises(fd.ErrorDeNegocio) as e:
                    r(sql, "lectura")
                self.assertIn(texto, str(e.exception))
        for sql, texto in [("UPDATE casos SET estado = 'x'", "sin WHERE"), ("DELETE FROM casos", "sin WHERE"), ("DROP TABLE casos", "solo acepta INSERT"),
                           ("INSERT INTO t VALUES (1); DROP TABLE t", "UNA sentencia"), ("SELECT 1", "solo acepta INSERT")]:
            with self.subTest(sql=sql):
                with self.assertRaises(fd.ErrorDeNegocio) as e:
                    r(sql, "escritura")
                self.assertIn(texto, str(e.exception))
        self.assertEqual(r("UPDATE casos SET estado = 'x'", "escritura", permitir_sin_where=True)[1], [])
        # MySQL: los comentarios ejecutables /*! … */ se quitan y los % se escapan para PyMySQL
        self.assertNotIn("DROP", r("SELECT 1 /*! ; DROP TABLE t */", "lectura", "mysql")[0])
        self.assertEqual(fd.c_sql._a_pymysql("SELECT '50%' AS p, x % 2 FROM t WHERE id = :id", ["id"]),
                         "SELECT '50%%' AS p, x %% 2 FROM t WHERE id = %(id)s")

    def test_consulta_parametros_y_topes(self):
        d = manual(nodo("q", "sql_consulta", credencial="base", consulta="SELECT id, documento, monto FROM casos WHERE estado = :estado ORDER BY id",
                        parametros={"estado": "{{entrada.estado}}"}, salida="filas"),
                   campos=[{"nombre": "estado", "obligatorio": True}],
                   aristas=[{"desde": "inicio", "hacia": "q"}, {"desde": "q", "hacia": "fin"}])
        self.assertEqual(self.problemas(d), [])
        r, ev = self.correr_orden(d, entrada={"estado": "pendiente"})
        self.assertEqual(r["variables"]["filas"], [{"id": 1, "documento": "111", "monto": 100.0}, {"id": 2, "documento": "222", "monto": 250.0}])
        # inyección: el valor va como parámetro, nunca como SQL
        r, _ = self.correr_orden(d, entrada={"estado": "x' OR '1'='1"})
        self.assertEqual(r["variables"]["filas"], [])
        d["nodos"][1]["config"]["max_filas"] = 1
        r, _ = self.correr_orden(d, entrada={"estado": "pendiente"})
        self.assertEqual(len(r["variables"]["filas"]), 1)
        self.assertTrue(any("se cortó" in a for a in r["avisos"]))
        # validación: plantillas en el SQL, parámetros que faltan, varias sentencias
        for conf, texto in [({"consulta": "SELECT * FROM casos WHERE documento = '{{entrada.doc}}'"}, "inyección"),
                            ({"consulta": "SELECT * FROM casos WHERE id = :id"}, "no está en parametros"),
                            ({"consulta": "SELECT 1; SELECT 2"}, "UNA sentencia"), ({"consulta": "SELECT 1", "credencial": "Clave Secreta"}, "NOMBRE")]:
            with self.subTest(conf=conf):
                p = fd._validar_config(nodo("q", "sql_consulta", **{"credencial": "base", **conf}))
                self.assertTrue(any(texto in x for x in p), p)

    def test_solo_lectura_aunque_se_cuele(self):
        # aunque el revisor fallara, SQLite abre en modo lectura y su autorizador niega lo demás
        with self.assertRaises(fd.ErrorDeNegocio):
            fd.c_sql._sqlite({"ruta": "base.db"}, "DELETE FROM casos", {}, "lectura", 10, 5)
        with self.assertRaises(fd.ErrorDeNegocio):
            fd.c_sql._sqlite({"ruta": "base.db"}, "ATTACH DATABASE 'otra.db' AS o", {}, "lectura", 10, 5)
        self.assertEqual(self.filas_bd(), ["pendiente", "pendiente", "cerrado"])
        with self.assertRaises(fd.ErrorDeNegocio):
            fd.c_sql.ejecutar("sqlite", {"ruta": "../fuera.db"}, "SELECT 1", {}, "lectura", 10, 5)
        # tiempo agotado: una consulta eterna se corta
        with self.assertRaises(TimeoutError):
            fd.c_sql.ejecutar("sqlite", {"ruta": "base.db"}, "WITH RECURSIVE r(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM r) SELECT max(x) FROM r",
                              {}, "lectura", 10, 1)

    def test_ejecutar_bloqueado_sin_permiso_y_real_con_permiso(self):
        def d(credencial):
            return manual(nodo("u", "sql_ejecutar", credencial=credencial, sentencia="UPDATE casos SET estado = :e WHERE id = :id",
                               parametros={"e": "atendido", "id": "1"}, salida="res"))
        r, ev = self.correr_orden(d("base"))
        self.assertEqual(self.filas_bd(), ["pendiente", "pendiente", "cerrado"])  # no cambió nada
        esc = next(e for e in ev if e["tipo"] == "escritura")
        self.assertTrue(esc["bloqueada"] and esc["simulada"])
        self.assertIn("no tiene la escritura habilitada", esc["motivo"])
        self.assertTrue(r["variables"]["res"]["bloqueado"])
        self.assertTrue(r["escrituras"][0]["bloqueado"])
        r, ev = self.correr_orden(d("base-escribe"))
        self.assertEqual(self.filas_bd(), ["atendido", "pendiente", "cerrado"])
        self.assertEqual(r["variables"]["res"]["afectadas"], 1)
        self.assertFalse(next(e for e in ev if e["tipo"] == "escritura")["bloqueada"])
        # tipo de credencial equivocado y credencial que no existe (o de otro flujo: el servidor no la manda)
        r, _ = self.correr_orden(d("buzon"), exigir_ok=False, credenciales={"buzon": cred("imap")})
        self.assertIn("es de tipo imap", r["motivo"])
        r, _ = self.correr_orden(d("ajena"), exigir_ok=False)
        self.assertIn("no existe o este flujo no la puede usar", r["motivo"])


# ------------------------------------------------------- bucles y lógica

class Bucles(Plataforma):
    def flujo_bucle(self, **conf_bucle):
        """entrada_manual → sql_consulta → para_cada (expresion → tabla_guardar) → salida."""
        nodos = [nodo("inicio", "entrada_manual"),
                 nodo("q", "sql_consulta", credencial="base", consulta="SELECT id, monto FROM casos ORDER BY id", salida="filas"),
                 nodo("cada", "para_cada", **{"lista": "filas", "variable": "fila", "salida": "resultados", **conf_bucle}),
                 nodo("total", "expresion", expresion="round(fila.monto * 1.19, 2)", salida="total"),
                 nodo("anotar", "tabla_guardar", tabla="totales", clave="{{fila.id}}", datos={"total": "{{total}}", "fila": "{{fila}}"}),
                 nodo("fin", "salida", desenlace="manual", motivo="{{resultados}}"), nodo("vacio", "salida", desenlace="descartar")]
        aristas = [{"desde": "inicio", "hacia": "q"}, {"desde": "q", "hacia": "cada", "cuando": "ok"}, {"desde": "q", "hacia": "vacio", "cuando": "vacio"},
                   {"desde": "cada", "hacia": "total", "cuando": "cada"}, {"desde": "total", "hacia": "anotar"}, {"desde": "cada", "hacia": "fin", "cuando": "listo"}]
        return flujo(nodos, aristas)

    def test_para_cada_con_expresion_y_tabla(self):
        d = self.flujo_bucle()
        self.assertEqual(self.problemas(d), [])
        r, ev = self.correr_orden(d)
        res = r["variables"]["resultados"]
        self.assertEqual([x["total"] for x in res], [119.0, 297.5, 95.2])
        filas = fd.c_tablas.consultar("flujo-prueba", "totales", orden="clave")
        self.assertEqual([(f["clave"], f["total"]) for f in filas], [("1", 119.0), ("2", 297.5), ("3", 95.2)])
        self.assertEqual(filas[0]["fila"], {"id": 1, "monto": 100.0})  # {{fila}} conserva el objeto
        # recoger y la tabla en modo insertar (sale por «existente» la segunda vez)
        d = self.flujo_bucle(recoger="{{fila.id}}")
        d["nodos"][4]["config"]["modo"] = "insertar"
        d["nodos"].append(nodo("ya", "fin_ciclo"))
        d["aristas"] += [{"desde": "anotar", "hacia": "ya", "cuando": "existente"}]
        self.assertEqual(self.problemas(d), [])
        r, ev = self.correr_orden(d)
        self.assertEqual(r["variables"]["resultados"], [1, 2, 3])
        self.assertTrue(all(e["salida"] == "existente" for e in ev if e["tipo"] == "etapa_fin" and e["etapa"] == "anotar"))

    def test_topes_de_vueltas_y_eventos_acotados(self):
        lista = list(range(10))
        d = manual(nodo("cada", "para_cada", lista="entrada.items", variable="x", salida="dobles"),
                   nodo("doble", "expresion", expresion="x * 2", salida="y"), campos=[{"nombre": "items", "tipo": "lista"}],
                   aristas=[{"desde": "inicio", "hacia": "cada"}, {"desde": "cada", "hacia": "doble", "cuando": "cada"}, {"desde": "cada", "hacia": "fin", "cuando": "listo"}])
        self.assertEqual(self.problemas(d), [])
        r, ev = self.correr_orden(d, entrada={"items": lista})
        self.assertEqual([v["y"] for v in r["variables"]["dobles"]], [x * 2 for x in lista])
        visibles = [e for e in ev if e["tipo"] == "etapa_inicio" and e["etapa"] == "doble"]
        self.assertEqual(len(visibles), fd.VUELTAS_VISIBLES + 1)  # las primeras y la última
        # tope de vueltas: por defecto 100, tope duro 1000
        r, _ = self.correr_orden(d, exigir_ok=False, entrada={"items": list(range(101))})
        self.assertIn("tope de vueltas es 100", r["motivo"])
        self.assertTrue(any("entre 1 y 1000" in x for x in fd._validar_config(nodo("c", "para_cada", lista="x", max_iteraciones=5000))))
        d["nodos"][1]["config"]["max_iteraciones"] = 1000
        r, _ = self.correr_orden(d, exigir_ok=False, entrada={"items": list(range(1001))})
        self.assertIn("tope de vueltas es 1000", r["motivo"])

    def test_cuerpo_con_ramas_fin_ciclo_fallos_y_salida(self):
        nodos = [nodo("cada", "para_cada", lista="entrada.items", variable="x", salida="res", al_fallar="continuar"),
                 nodo("es_par", "expresion", expresion="x % 2 == 0", como_condicion=True),
                 nodo("div", "expresion", expresion="10 / (x - 4)", salida="cociente"),
                 nodo("salta", "fin_ciclo")]
        aristas = [{"desde": "inicio", "hacia": "cada"}, {"desde": "cada", "hacia": "es_par", "cuando": "cada"},
                   {"desde": "es_par", "hacia": "div", "cuando": "si"}, {"desde": "es_par", "hacia": "salta", "cuando": "no"},
                   {"desde": "cada", "hacia": "fin", "cuando": "listo"}]
        d = manual(*nodos, aristas=aristas, campos=[{"nombre": "items", "tipo": "lista"}])
        self.assertEqual(self.problemas(d), [])  # «div» sin arista termina la vuelta; fin_ciclo dentro del cuerpo
        r, _ = self.correr_orden(d, entrada={"items": [2, 3, 4, 6]})
        res = r["variables"]["res"]
        self.assertEqual(res[0]["cociente"], -5.0)
        self.assertEqual(res[1], 3)  # impar: fin_ciclo, sin cambios → el elemento
        self.assertEqual(res[2]["indice"], 2)  # 10 / 0: con al_fallar continuar, la vuelta se anota y sigue
        self.assertIn("división por cero", res[2]["error"])
        self.assertEqual(res[3]["cociente"], 5.0)
        # sin al_fallar continuar, la misma vuelta tumba la corrida
        d["nodos"][1]["config"]["al_fallar"] = "detener"
        r, _ = self.correr_orden(d, exigir_ok=False, entrada={"items": [2, 4]})
        self.assertEqual(r["accion"], "error")
        # el cuerpo llega a una salida: termina todo el flujo
        nodos2 = [nodo("cada", "para_cada", lista="entrada.items", variable="x"), nodo("c", "expresion", expresion="x > 1", como_condicion=True),
                  nodo("corta", "salida", desenlace="descartar", motivo="paró en {{x}}")]
        a2 = [{"desde": "inicio", "hacia": "cada"}, {"desde": "cada", "hacia": "c", "cuando": "cada"}, {"desde": "c", "hacia": "corta", "cuando": "si"},
              {"desde": "cada", "hacia": "fin", "cuando": "listo"}]
        d2 = manual(*nodos2, aristas=a2, campos=[{"nombre": "items", "tipo": "lista"}])
        self.assertEqual(self.problemas(d2), [])
        r, _ = self.correr_orden(d2, entrada={"items": [1, 2, 3]})
        self.assertEqual((r["accion"], r["motivo"]), ("descartado", "paró en 2"))
        # fin_ciclo fuera de un cuerpo, para_cada sin «listo»
        mal = manual(nodo("f", "fin_ciclo"))
        self.assertProblema(mal, "fin_ciclo solo va dentro")
        mal = manual(nodo("cada", "para_cada", lista="entrada"), aristas=[{"desde": "inicio", "hacia": "cada"}, {"desde": "cada", "hacia": "fin", "cuando": "cada"}])
        self.assertProblema(mal, "necesita una arista «listo»")

    def test_paralelo_y_unir(self):
        nodos = [nodo("ramas", "paralelo", ramas=["a", "b"]), nodo("ta", "expresion", expresion="1 + 1", salida="va"),
                 nodo("tb", "expresion", expresion="'b' * 3", salida="vb"), nodo("junta", "unir", salida="todo"),
                 nodo("usa", "expresion", expresion="todo.a.va + len(todo.b.vb)", salida="suma")]
        aristas = [{"desde": "inicio", "hacia": "ramas"}, {"desde": "ramas", "hacia": "ta", "cuando": "a"}, {"desde": "ramas", "hacia": "tb", "cuando": "b"},
                   {"desde": "ta", "hacia": "junta"}, {"desde": "tb", "hacia": "junta"}, {"desde": "junta", "hacia": "usa"}, {"desde": "usa", "hacia": "fin"}]
        d = manual(*nodos, aristas=aristas)
        self.assertEqual(self.problemas(d), [])
        r, _ = self.correr_orden(d)
        self.assertEqual(r["variables"]["todo"], {"a": {"va": 2}, "b": {"vb": "bbb"}})
        self.assertEqual(r["variables"]["suma"], 5)
        mal = manual(nodo("ramas", "paralelo", ramas=["a", "b"]), nodo("ta", "expresion", expresion="1"),
                     aristas=[{"desde": "inicio", "hacia": "ramas"}, {"desde": "ramas", "hacia": "ta", "cuando": "a"}, {"desde": "ta", "hacia": "fin"}])
        self.assertProblema(mal, "falta arista para la rama «b»")

    def test_esperar_json_y_fecha(self):
        d = manual(nodo("e", "esperar", segundos=2.5), nodo("p", "json", op="parsear", de="entrada.contenido", salida="obj"),
                   nodo("s", "json", op="serializar", de="obj", salida="txt"), nodo("sel", "json", op="seleccionar", de="obj", ruta="items[estado=ok].id", salida="id"),
                   nodo("f1", "fecha", op="sumar", valor="2026-11-06", cantidad=15, unidad="dias", formato="%Y-%m-%d", salida="vence"),
                   nodo("f2", "fecha", op="diferencia", valor="01/11/2026", hasta="2026-11-06", unidad="dias", salida="dias"),
                   nodo("f3", "fecha", op="ahora", salida="ahora"),
                   campos=[{"nombre": "contenido"}])
        self.assertEqual(self.problemas(d), [])
        r, _ = self.correr_orden(d, entrada={"contenido": '{"items": [{"id": 1, "estado": "no"}, {"id": 2, "estado": "ok"}]}'})
        v = r["variables"]
        self.assertEqual(self.esperas, [2.5])  # _dormir de mentira
        self.assertEqual(v["id"], 2)
        self.assertEqual(json.loads(v["txt"])["items"][0]["id"], 1)
        self.assertEqual((v["vence"], v["dias"]), ("2026-11-21", 5.0))
        self.assertTrue(v["ahora"].endswith("-05:00"))  # America/Bogota
        self.assertTrue(any("entre 0 y 60" in x for x in fd._validar_config(nodo("e", "esperar", segundos=600))))

    def test_llamar_flujo(self):
        hijo = manual(nodo("t", "expresion", expresion="entrada.n * 10", salida="por_diez"), campos=[{"nombre": "n", "tipo": "numero", "obligatorio": True}],
                      fin=nodo("fin", "salida", desenlace="responder", plantilla_respuesta="{{por_diez}}"))
        hijo["id"] = "hijo"
        padre = manual(nodo("llama", "llamar_flujo", flujo="hijo", entrada={"n": "{{entrada.n}}"}, salida="r"), campos=[{"nombre": "n"}])
        padre["id"] = "padre"
        r, ev = self.correr_orden(padre, entrada={"n": 4}, subflujos={"hijo": {"definicion": hijo}})
        self.assertEqual((r["variables"]["r"]["accion"], r["variables"]["r"]["respuesta"]), ("respondido", "40"))
        self.assertFalse(any(e["tipo"] == "etapa_inicio" and e["etapa"] == "t" for e in ev))  # los pasos del hijo no ensucian el lienzo
        r, _ = self.correr_orden(padre, exigir_ok=False, entrada={"n": 4})
        self.assertIn("no existe o no es declarativo", r["motivo"])
        # recursión y profundidad
        rec = manual(nodo("llama", "llamar_flujo", flujo="padre"))
        rec["id"] = "padre"
        r, _ = self.correr_orden(rec, exigir_ok=False, subflujos={"padre": {"definicion": rec}})
        self.assertIn("ciclo", r["motivo"])
        cadena_ = {}
        for i in range(5):
            f = manual(nodo("llama", "llamar_flujo", flujo=f"f{i + 1}")) if i < 4 else manual()
            f["id"] = f"f{i}"
            cadena_[f"f{i}"] = {"definicion": f}
        r, _ = self.correr_orden(cadena_["f0"]["definicion"], exigir_ok=False, subflujos=cadena_, flujo="f0")
        self.assertIn("niveles", json.dumps(r, ensure_ascii=False))


# ------------------------------------------------------------- expresiones

class Expresiones(unittest.TestCase):
    def test_lo_permitido(self):
        ev = fd.c_expresion.evaluar
        v = {"fila": {"monto": 10, "nombre": " Ana "}, "xs": [3, 1, 2], "t": "a,b"}
        casos = [("fila.monto * 1.5", 15.0), ("fila['monto'] + 1", 11), ("len(xs) > 2 and max(xs) == 3", True), ("sum(xs) / len(xs)", 2.0),
                 ("round(10 / 3, 2)", 3.33), ("fila.nombre.strip().upper()", "ANA"), ("t.split(',')", ["a", "b"]), ("'-'.join(xs)", "3-1-2"),
                 ("'x' if xs[0] > 2 else 'y'", "x"), ("xs[1:]", [1, 2]), ("{'a': 1}.get('a')", 1), ("sorted(xs)", [1, 2, 3]),
                 ("numero('1.234,5') + 1", 1235.5), ("2 ** 10", 1024), ("not true", False), ("contiene('Hola Mundo', 'mundo')", True),
                 ("null == None", True), ("3 in xs", True), ("min(1, 2)", 1)]
        for expr, esperado in casos:
            with self.subTest(expr=expr):
                self.assertEqual(ev(expr, v), esperado)

    def test_escapes_del_sandbox(self):
        ev = fd.c_expresion.evaluar
        v = {"x": "texto", "d": {}}
        for expr in ["__import__('os').system('id')", "().__class__.__bases__[0].__subclasses__()", "x.__class__", "x.__len__()",
                     "d.__class__", "[c for c in x]", "{c: 1 for c in x}", "(c for c in x)", "lambda: 1", "f'{x}'", "open('/etc/passwd')",
                     "eval('1')", "exec('1')", "getattr(x, 'upper')", "globals()", "__builtins__", "_privada", "x._algo",
                     "(y := 1)", "'a' * 10**7", "2 ** 10000", "9 ** 9 ** 9", "x.format(1)", "d.update({'a': 1})", "'%s' % x",
                     "type(x)", "{1, 2}", "*x", "'a'.zfill(10**9)", "x[0:1].__class__", "1/0", "y_no_existe"]:
            with self.subTest(expr=expr):
                with self.assertRaises(fd.ErrorDeNegocio):
                    ev(expr, v)
        with self.assertRaises(fd.ErrorDeNegocio):
            ev("1 + " * 400 + "1", {})  # demasiado larga
        self.assertEqual(fd.c_expresion.nombres("fila.monto > tope and len(xs)"), {"fila", "tope", "xs"})


# ----------------------------------------------- archivos, hojas y tablas

class Archivos(Plataforma):
    def test_guardar_y_leer_dentro_del_espacio(self):
        filas = [{"doc": "111", "monto": 10}, {"doc": "222", "monto": 20}]
        d = manual(nodo("g", "archivo_guardar", ruta="reportes/{{entrada.nombre}}.csv", de="entrada.filas", formato="csv"),
                   nodo("g2", "archivo_guardar", ruta="reportes/{{entrada.nombre}}.csv", de="entrada.filas", formato="csv", modo="anexar"),
                   nodo("j", "archivo_guardar", ruta="reportes/datos.json", de="entrada.filas", formato="json"),
                   nodo("l", "archivo_leer", ruta="reportes/{{entrada.nombre}}.csv", salida="leido"),
                   nodo("lj", "archivo_leer", ruta="reportes/datos.json", salida="json_leido"),
                   campos=[{"nombre": "nombre"}, {"nombre": "filas", "tipo": "lista"}])
        self.assertEqual(self.problemas(d), [])
        r, _ = self.correr_orden(d, entrada={"nombre": "hoy", "filas": filas})
        self.assertEqual(r["variables"]["leido"]["filas"], [{"doc": "111", "monto": "10"}, {"doc": "222", "monto": "20"}] * 2)
        self.assertEqual(r["variables"]["json_leido"]["datos"], filas)
        self.assertTrue((self.archivos / "reportes" / "hoy.csv").exists())

    def test_rutas_que_salen_se_rechazan(self):
        for ruta in ["../fuera.txt", "/etc/passwd", "a/../../fuera.txt", "C:/Windows/win.ini", "~/x", "reportes/../../x"]:
            with self.subTest(ruta=ruta):
                with self.assertRaises(fd.ErrorDeNegocio):
                    fd.c_archivos.guardar(ruta, "x")
                with self.assertRaises(fd.ErrorDeNegocio):
                    fd.c_archivos.leer(ruta)
                self.assertTrue(fd._validar_config(nodo("g", "archivo_guardar", ruta=ruta, contenido="x")))
        # con plantilla, en la corrida
        d = manual(nodo("g", "archivo_guardar", ruta="{{entrada.ruta}}", contenido="x"), campos=[{"nombre": "ruta"}])
        r, _ = self.correr_orden(d, exigir_ok=False, entrada={"ruta": "../../escape.txt"})
        self.assertEqual(r["accion"], "error")
        self.assertIn("espacio de archivos", r["motivo"])
        self.assertFalse((Path(self.tmp.name) / "escape.txt").exists())
        # un enlace simbólico que apunta afuera tampoco
        try:
            (self.archivos / "enlace").symlink_to(Path(self.tmp.name))
        except (OSError, NotImplementedError):
            return
        with self.assertRaises(fd.ErrorDeNegocio):
            fd.c_archivos.leer("enlace/estado/x")
        # no_existe
        r, _ = self.correr_orden(manual(nodo("l", "archivo_leer", ruta="no/esta.txt")))
        self.assertEqual(next(e for e in _ if e["tipo"] == "etapa_fin" and e["etapa"] == "l")["salida"], "no_existe")

    def test_hoja_leer_xlsx_csv_y_adjunto(self):
        import openpyxl
        libro = openpyxl.Workbook()
        h = libro.active
        h.title = "Datos"
        h.append(["documento", "monto"])
        h.append(["111", 10])
        h.append(["222", 20.5])
        libro.save(str(self.archivos / "planilla.xlsx"))
        d = manual(nodo("h", "hoja_leer", origen="archivo", archivo="planilla.xlsx", hoja="Datos", salida="filas"))
        r, _ = self.correr_orden(d)
        self.assertEqual(r["variables"]["filas"], [{"documento": "111", "monto": 10}, {"documento": "222", "monto": 20.5}])
        # del adjunto del correo
        d = cadena(nodo("h", "hoja_leer", origen="adjunto", adjunto="certificado", encabezados=False, salida="filas"))
        r, _ = self.correr_orden(d, caso=CASO)
        self.assertEqual(r["variables"]["filas"][0], {"columna_1": "CERTIFICADO DE INCAPACIDAD"})
        # una URL de un host que no está permitido no se pide
        d = manual(nodo("h", "hoja_leer", origen="url", url="https://datos.positiva.gov.co/x.csv"))
        r, _ = self.correr_orden(d, exigir_ok=False)
        self.assertIn("Positiva", r["motivo"])

    def test_tabla_integrada(self):
        t = fd.c_tablas
        t.guardar("flujo-a", "vistos", "k1", {"n": 1, "ok": True})
        self.assertEqual(t.guardar("flujo-a", "vistos", "k1", {"n": 2}, "insertar")[1], False)
        t.guardar("flujo-a", "vistos", "k1", {"m": 3}, "combinar")
        self.assertEqual({k: v for k, v in t.consultar("flujo-a", "vistos", "k1")[0].items() if not k.startswith("_")}, {"clave": "k1", "n": 1, "ok": True, "m": 3})
        self.assertEqual(len(t.consultar("flujo-a", "vistos", filtro={"ok": True})), 1)
        self.assertEqual(t.consultar("flujo-b", "vistos"), [])  # cada flujo la suya
        for malo in ("Mayus", "con espacio", "1a", "a;drop"):
            with self.assertRaises(fd.ErrorDeNegocio):
                t.guardar("flujo-a", malo, "k", {})
        with self.assertRaises(fd.ErrorDeNegocio):
            t.consultar("flujo-a", "vistos", filtro={"x') OR 1=1 --": 1})
        with self.assertRaises(fd.ErrorDeNegocio):
            t.guardar("../etc", "x", "k", {})
        with self.assertRaises(fd.ErrorDeNegocio):
            t.guardar("flujo-a", "grande", "k", {"x": "a" * 70000})


# ------------------------------------------------------------- correo y Drive

class CorreoYDrive(Plataforma):
    def setUp(self):
        super().setUp()
        self.imap = ImapFalso([correo_mime("1", "Hola", "a@ejemplo.test", adjunto=("datos.csv", b"x,y\n1,2\n")), correo_mime("2", "Otro", "b@ejemplo.test")])
        self.drive = DriveFalso()
        SmtpFalso.enviados.clear()
        self._previos = (fd.c_correo.fabrica_imap, fd.c_correo.fabrica_smtp, fd.c_correo.pedir_https, fd.c_drive.fabrica)
        fd.c_correo.fabrica_imap = lambda *a: self.imap
        fd.c_correo.fabrica_smtp = lambda *a: SmtpFalso()
        fd.c_drive.fabrica = lambda valores, timeout: self.drive
        self.creds = {**self.creds,
                      "buzon": cred("imap", secretos=["clave"], host="imap.ejemplo.test", usuario="ops@ejemplo.test", clave=CLAVE_SQL),
                      "smtp": cred("smtp", secretos=["clave"], host="smtp.ejemplo.test", usuario="ops@ejemplo.test", clave=CLAVE_SQL),
                      "smtp-ok": cred("smtp", True, secretos=["clave"], host="smtp.ejemplo.test", usuario="ops@ejemplo.test", clave=CLAVE_SQL),
                      "drive": cred("google_servicio", secretos=["json"], json='{"type": "service_account"}'),
                      "drive-ok": cred("google_servicio", True, secretos=["json"], json='{"type": "service_account"}'),
                      "graph-pqrd": cred("microsoft_graph", True, secretos=["client_secret"], tenant="t1", client_id="c", client_secret=TOKEN,
                                         buzon="servicioalcliente@positiva.gov.co")}

    def tearDown(self):
        fd.c_correo.fabrica_imap, fd.c_correo.fabrica_smtp, fd.c_correo.pedir_https, fd.c_drive.fabrica = self._previos
        super().tearDown()

    def test_correo_leer_imap_con_adjuntos_y_para_cada(self):
        d = manual(nodo("leer", "correo_leer", credencial="buzon", incluir_adjuntos=True, salida="correos"),
                   nodo("cada", "para_cada", lista="correos", variable="c", recoger="{{c.asunto}}", salida="asuntos"),
                   aristas=[{"desde": "inicio", "hacia": "leer"}, {"desde": "leer", "hacia": "cada", "cuando": "ok"},
                            {"desde": "leer", "hacia": "fin", "cuando": "vacio"}, {"desde": "cada", "hacia": "fin", "cuando": "listo"},
                            {"desde": "cada", "hacia": "nada", "cuando": "cada"}])
        d["nodos"].insert(3, nodo("nada", "fin_ciclo"))
        self.assertEqual(self.problemas(d), [])
        r, _ = self.correr_orden(d)
        self.assertEqual(r["variables"]["asuntos"], ["Otro", "Hola"])  # los más recientes primero
        adj = r["variables"]["correos"][1]["adjuntos"][0]
        self.assertEqual(adj["nombre"], "datos.csv")
        self.assertIn("x", adj["texto"])
        self.assertTrue(all(x[1] is True for x in self.imap.llamadas if x[0] == "select"))
        self.assertNotIn(CLAVE_SQL, self.salida.getvalue())

    def test_buzones_de_positiva_bloqueados_siempre(self):
        llamadas = []
        fd.c_correo.pedir_https = lambda *a: llamadas.append(a) or (200, b"{}")
        r, _ = self.correr_orden(manual(nodo("leer", "correo_leer", credencial="graph-pqrd")), exigir_ok=False)
        self.assertIn("bloqueado por seguridad", r["motivo"])
        self.assertEqual(llamadas, [])  # ni siquiera pidió el token
        os.environ["PQRD_BUZON_EXTRA"] = "otro.buzon@ejemplo.test"
        try:
            self.assertIsNotNone(fd.direccion_vetada("otro.buzon@ejemplo.test"))
        finally:
            os.environ.pop("PQRD_BUZON_EXTRA")
        for d in ("notificacionesjudiciales@positiva.gov.co", "Alguien <x@positiva.gov.co>", "y@sub.positiva.gov.co"):
            self.assertIsNotNone(fd.direccion_vetada(d), d)
        self.assertIsNone(fd.direccion_vetada("persona@ejemplo.test"))
        for h in ("positiva.gov.co", "api.positiva.gov.co", "ms-sgdea-prd.proyectos-3t.tech", "sgdea-prod.ejemplo", "metadata.google.internal"):
            self.assertIsNotNone(fd.host_vetado(h), h)

    def test_correo_enviar_bloqueado_real_y_destinos_vetados(self):
        d = manual(nodo("env", "correo_enviar", credencial="smtp", para="{{entrada.para}}", asunto="Hola {{entrada.nombre}}", cuerpo="Su total es {{entrada.total}}"),
                   campos=[{"nombre": "para"}, {"nombre": "nombre"}, {"nombre": "total"}])
        entrada = {"para": "persona@ejemplo.test", "nombre": "Ana", "total": "10"}
        r, ev = self.correr_orden(d, entrada=entrada)
        self.assertEqual(SmtpFalso.enviados, [])
        self.assertTrue(r["variables"]["env"]["bloqueado"])
        esc = next(e for e in ev if e["tipo"] == "escritura")
        self.assertTrue(esc["bloqueada"])
        self.assertEqual(esc["detalle"]["asunto"], "Hola Ana")
        d["nodos"][1]["config"]["credencial"] = "smtp-ok"
        r, ev = self.correr_orden(d, entrada=entrada)
        self.assertEqual(SmtpFalso.enviados, [{"de": "ops@ejemplo.test", "para": ["persona@ejemplo.test"], "asunto": "Hola Ana", "cuerpo": "Su total es 10\n"}])
        self.assertFalse(r["variables"]["env"]["bloqueado"])
        # a Positiva nunca, aunque la credencial permita escribir
        for para in ("servicioalcliente@positiva.gov.co", "a@ejemplo.test, jefe@positiva.gov.co"):
            r, _ = self.correr_orden(d, exigir_ok=False, entrada={**entrada, "para": para})
            self.assertIn("bloqueado por seguridad", r["motivo"])
        self.assertEqual(len(SmtpFalso.enviados), 1)
        self.assertNotIn(CLAVE_SQL, self.salida.getvalue())

    def test_drive_listar_leer_subir(self):
        d = manual(nodo("l", "drive_listar", credencial="drive", carpeta="1CARPETACARPETA", nombre="o'k", salida="archivos"),
                   nodo("r", "drive_leer", credencial="drive", archivo="1AAAAAAAAAAAAA", salida="notas"),
                   nodo("h", "hoja_leer", origen="drive", credencial="drive", archivo="1HOJAHOJAHOJA1", salida="filas"),
                   nodo("s", "drive_subir", credencial="drive", carpeta="1CARPETACARPETA", nombre="r.csv", de="filas", formato="csv", salida="subido"),
                   aristas=None)
        d["aristas"] = [{"desde": a, "hacia": b} for a, b in (("inicio", "l"), ("l", "r"), ("r", "h"), ("h", "s"), ("s", "fin"))]
        self.assertEqual(self.problemas(d), [])
        r, _ = self.correr_orden(d)
        v = r["variables"]
        self.assertEqual(len(v["archivos"]), 2)
        self.assertIn("name contains 'o\\'k'", self.drive.consultas[0])  # comilla escapada en la consulta
        self.assertEqual(v["notas"]["texto"], "hola desde drive")
        self.assertEqual(v["filas"], [{"doc": "111", "monto": "10"}, {"doc": "222", "monto": "20"}])
        self.assertEqual(self.drive.subidos, [])  # bloqueado
        self.assertTrue(v["subido"]["bloqueado"])
        for n in d["nodos"]:
            if n["config"].get("credencial") == "drive":
                n["config"]["credencial"] = "drive-ok"
        r, _ = self.correr_orden(d)
        self.assertEqual(self.drive.subidos[0][0:2], ("r.csv", "1CARPETACARPETA"))
        self.assertEqual(r["variables"]["subido"]["id"], "1NUEVONUEVONUE")
        r, _ = self.correr_orden(manual(nodo("l", "drive_listar", credencial="drive", carpeta="x' or '1'='1")), exigir_ok=False)
        self.assertIn("no es un id de Drive", r["motivo"])


# --------------------------------------------- http con credencial guardada

class HttpConCredencial(Http):
    def correr_orden(self, defi, creds, exigir_ok=True):
        self.salida.seek(0)
        self.salida.truncate()
        asyncio.run(fd.procesar({"cmd": "procesar", "corrida": "x", "caso": CASO, "definicion": defi, "credenciales": creds}))
        eventos = [json.loads(linea[len(fd.MARCA):]) for linea in self.salida.getvalue().split(SALTO) if linea.startswith(fd.MARCA)]
        return next(e for e in eventos if e["tipo"] == "resultado"), eventos

    def test_post_solo_con_escritura_habilitada_y_al_host_de_la_credencial(self):
        fd._IPS_PERMITIDAS_PRUEBAS.add("127.0.0.1")
        os.environ["HTTP_HOSTS_PERMITIDOS"] = ""
        d = self.flujo_http(metodo="POST", cuerpo={"a": "{{correo.asunto}}"}, credencial="api")
        bloqueada = {"api": cred("http_bearer", secretos=["token"], token=TOKEN, hosts="127.0.0.1")}
        r, ev = self.correr_orden(d, bloqueada)
        self.assertEqual(_Servidor.vistas, [])
        self.assertTrue(r["variables"]["resp"]["simulado"])
        self.assertIn("no tiene la escritura habilitada", r["variables"]["resp"]["motivo"])
        habilitada = {"api": cred("http_bearer", True, secretos=["token"], token=TOKEN, hosts="127.0.0.1")}
        r, ev = self.correr_orden(d, habilitada)
        self.assertEqual(_Servidor.vistas[0]["auth"], f"Bearer {TOKEN}")
        self.assertFalse(next(e for e in ev if e["tipo"] == "escritura")["bloqueada"])
        # GET con credencial: sale al host de la credencial aunque no esté en HTTP_HOSTS_PERMITIDOS
        r, _ = self.correr_orden(self.flujo_http(credencial="api"), bloqueada)
        self.assertEqual(r["variables"]["resp"]["status"], 200)
        # la credencial no se manda a otro host
        r, _ = self.correr_orden(self.flujo_http(credencial="api"), {"api": cred("http_bearer", True, token=TOKEN, hosts="api.ejemplo.test")})
        self.assertIn("solo se puede mandar a api.ejemplo.test", r["motivo"])
        # básica
        r, _ = self.correr_orden(self.flujo_http(credencial="basica"), {"basica": cred("http_basico", secretos=["clave"], usuario="u", clave=CLAVE_SQL, hosts="127.0.0.1")})
        self.assertTrue(_Servidor.vistas[-1]["auth"].startswith("Basic "))
        self.assertNotIn(TOKEN, self.salida.getvalue())
        self.assertNotIn(CLAVE_SQL, self.salida.getvalue())

    def test_positiva_bloqueado_aunque_este_permitido(self):
        os.environ["HTTP_HOSTS_PERMITIDOS"] = "api.positiva.gov.co,positiva.gov.co"
        d = self.flujo_http()
        d["nodos"][1]["config"]["url"] = "https://api.positiva.gov.co/x"
        r, _ = self.correr_orden(d, {})
        self.assertEqual(r["accion"], "descartado")
        self.assertIn("Positiva", r["motivo"])


# ------------------------------------------------- secretos y entorno por flujo

class SecretosYEntorno(Plataforma):
    def test_ningun_secreto_sale_en_eventos_resultado_ni_consola(self):
        creds = {**self.creds, "api": cred("http_bearer", secretos=["token"], token=TOKEN, hosts="api.ejemplo.test"),
                 "base-clave": cred("postgres", secretos=["clave"], host="10.0.0.5", usuario="u", clave=CLAVE_SQL)}
        d = manual(nodo("t", "transformar", operaciones=[{"op": "plantilla", "plantilla": "token={{entorno.TOKEN_API}} url={{entorno.URL_BASE}}", "salida": "fuga"}]),
                   nodo("x", "expresion", expresion="entorno.TOKEN_API + '!'", salida="fuga2"),
                   nodo("n", "notificar", para="a@ejemplo.test", cuerpo="{{fuga}} {{fuga2}}"),
                   nodo("q", "sql_consulta", credencial="base-clave", consulta="SELECT 1", continuar_si_falla=True))
        orden = {"entorno": {"TOKEN_API": TOKEN, "URL_BASE": "https://api.ejemplo.test"}, "entorno_secretos": ["TOKEN_API"], "credenciales": creds}
        r, ev = self.correr_orden(d, **orden)
        texto = self.salida.getvalue()
        self.assertNotIn(TOKEN, texto)
        self.assertNotIn(CLAVE_SQL, texto)
        self.assertIn("token=••••", r["variables"]["fuga"])
        self.assertIn("https://api.ejemplo.test", r["variables"]["fuga"])  # lo que no es secreto sí se ve
        self.assertTrue(any("10.0.0.5" in a for a in r["avisos"]))  # el fallo (IP privada) sí se ve, la clave no
        # la consola (stderr de bibliotecas y trazas) pasa por la misma máscara
        fd._secretos_activos.add(TOKEN)
        buf = io.StringIO()
        fd._Enmascarado(buf).write(f"Traceback: clave {TOKEN} en la línea")
        fd._secretos_activos.clear()
        self.assertEqual(buf.getvalue(), "Traceback: clave •••• en la línea")

    def test_entorno_valida_nombres_y_resuelve(self):
        d = manual(nodo("g", "expresion", expresion="1", salida="x"),
                   fin=nodo("fin", "salida", desenlace="manual", motivo="{{entorno.REGION}} {{entorno.NO_ESTA}}"))
        self.assertTrue(any("no tiene la variable de entorno NO_ESTA" in p for p in fd.validar(d, entorno=["REGION"])))
        self.assertFalse(any("entorno.REGION" in p for p in fd.validar(d, entorno=["REGION"])))
        self.assertEqual([p for p in fd.validar(d) if "entorno" in p], [])  # sin nombres no se revisa
        self.assertTrue(any("MAYÚSCULAS" in p for p in fd.validar(manual(fin=nodo("fin", "salida", desenlace="manual", motivo="{{entorno.minus}}")))))
        self.assertTrue(any("no es un nombre de variable válido" in p for p in fd.validar(manual(nodo("g", "expresion", expresion="1", salida="entorno")))))
        r, _ = self.correr_orden(d, exigir_ok=False, entorno={"REGION": "andina"})
        self.assertEqual(r["accion"], "error")  # en la corrida, una variable de entorno que no existe impide correr
        r, _ = self.correr_orden(d, entorno={"REGION": "andina", "NO_ESTA": "x"})
        self.assertEqual(r["motivo"], "andina x")
        # CLI de validación con los nombres del entorno
        p = subprocess.run([sys.executable, str(AQUI / "flujo_declarativo.py"), "validar"], input=json.dumps({"definicion": d, "entorno": ["REGION"]}),
                           capture_output=True, text=True, encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout=120)
        self.assertTrue(any("NO_ESTA" in x for x in json.loads(p.stdout.strip().splitlines()[-1])), p.stdout + p.stderr)


# ------------------------------------------------------------- paridad con zod

class ParidadConElServidor(unittest.TestCase):
    """Los mismos tipos y los mismos ejemplos aceptados por el zod del servidor (si está compilado y hay node)."""

    def test_zod_acepta_los_ejemplos_y_conoce_los_tipos(self):
        import shutil
        dist = Path(os.environ.get("RUTA_SERVIDOR_DIST", "/app/servidor/dist")) / "flujos.js"
        node = shutil.which("node")
        if not node or not dist.exists():
            self.skipTest("no hay servidor compilado o node")
        ejemplos = [{"tipo": t, "config": c} for t, s in nd.TIPOS.items() for c in [s["ejemplo"]["config"], *s["ejemplos"]]]
        malos = [{"tipo": "sql_consulta", "config": {"credencial": "b"}}, {"tipo": "para_cada", "config": {"lista": "x", "max_iteraciones": 5000}},
                 {"tipo": "esperar", "config": {"segundos": 120}}, {"tipo": "programado", "config": {"cron": "1 2 3"}},
                 {"tipo": "correo_enviar", "config": {"credencial": "s", "para": "a@b.c", "asunto": "x"}},
                 {"tipo": "archivo_guardar", "config": {"ruta": "../x", "contenido": "x"}}, {"tipo": "expresion", "config": {}}]
        guion = ("const f = await import(process.argv[1]); const d = JSON.parse(process.argv[2]);"
                 "const r = (xs) => xs.map((e) => { const s = f.CONFIG_DE[e.tipo]; return s ? s.safeParse(e.config).success : null; });"
                 "console.log(JSON.stringify({tipos: [...f.TIPOS_DE_NODO], buenos: r(d.buenos), malos: r(d.malos)}));")
        p = subprocess.run([node, "--input-type=module", "-e", guion, dist.as_uri(), json.dumps({"buenos": ejemplos, "malos": malos})],
                           capture_output=True, text=True, encoding="utf-8", timeout=60)
        r = json.loads(p.stdout.strip().splitlines()[-1])
        self.assertEqual(set(r["tipos"]), fd.TIPOS)
        rechazados = [e for e, ok in zip(ejemplos, r["buenos"]) if not ok]
        self.assertEqual(rechazados, [])
        self.assertEqual(r["malos"], [False] * len(malos))
        for m in malos:  # y Python también los rechaza
            self.assertTrue(fd._validar_config({"id": "n", **m}), m)


if __name__ == "__main__":
    unittest.main(verbosity=2)
