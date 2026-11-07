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
# la caché de conversión (por huella) solo en sus propias pruebas, con su carpeta temporal
os.environ["CACHE_CONVERSION"] = "no"

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

    def __call__(self, modelo, sistema, tarea, esquema, temperatura, json_libre=False, **ia):
        self.llamadas.append({"modelo": modelo, "sistema": sistema, "tarea": tarea, "esquema": esquema, "temperatura": temperatura, "json": json_libre,
                              "proveedor": ia.get("proveedor") or "vertex", "credencial": ia.get("credencial"), "flujo": ia.get("flujo")})
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
        formatos = {"plantilla", "credencial", "variable", "expresion", "texto-largo", "cron", "proveedor-ia", "modelo-ia", "archivo-flujo"}
        claves = {"type", "properties", "required", "enum", "items", "description", "default", "minimum", "maximum", "title", "format",
                  "x-credencial-tipos", "x-comun", "x-largo", "x-legado", "additionalProperties", "minItems"}

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
                 "base-clave": cred("postgres", secretos=["clave"], host="127.0.0.1", usuario="u", clave=CLAVE_SQL)}
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
        self.assertTrue(any("127.0.0.1" in a for a in r["avisos"]))  # el fallo (loopback) sí se ve, la clave no
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


# ------------------------------------------- búsqueda robusta en todos los nodos

from conectores import busqueda as bq  # noqa: E402

MIXTOS = ["PETICIÓN", "peticion", "Petición  urgente", "peticiòn", "  petición "]


def desde_manual(nodos, aristas):
    """entrada_manual («inicio») seguida de estos nodos con estas aristas."""
    return flujo([nodo("inicio", "entrada_manual")] + list(nodos), aristas)


class BusquedaRobusta(Plataforma):
    """Mayúsculas, tildes, espacios y mezclas no impiden encontrar; varias condiciones, operadores nuevos y longitud."""

    def rama(self, conf, entrada, tipo="condicion"):
        """Corre entrada_manual → (condición) → «SI» o «NO»."""
        ns = [nodo("inicio", "entrada_manual"), nodo("c", tipo, **conf),
              nodo("si", "salida", desenlace="manual", motivo="SI"), nodo("no", "salida", desenlace="descartar", motivo="NO")]
        d = flujo(ns, [{"desde": "inicio", "hacia": "c"}, {"desde": "c", "hacia": "si", "cuando": "si"}, {"desde": "c", "hacia": "no", "cuando": "no"}])
        self.assertEqual(self.problemas(d), [], conf)
        r, ev = self.correr_orden(d, entrada=entrada)
        return r["motivo"] == "SI"

    def test_textos_mixtos_en_cada_operador_de_texto(self):
        T, F = True, False
        casos = [("contiene", {"valor": "petición"}, [T, T, T, T, T]),
                 ("igual", {"valor": "PETICION"}, [T, T, F, T, T]),
                 ("distinto", {"valor": "peticion"}, [F, F, T, F, F]),
                 ("no_contiene", {"valor": "peticion"}, [F, F, F, F, F]),
                 ("empieza_con", {"valor": "petició"}, [T, T, T, T, T]),
                 ("termina_con", {"valor": "CION"}, [T, T, F, T, T]),
                 ("palabra", {"valor": "Peticion"}, [T, T, T, T, T]),
                 ("contiene_alguna", {"valores": ["queja", "petición"]}, [T, T, T, T, T]),
                 ("contiene_todas", {"valor": "petición | URGENTE"}, [F, F, T, F, F]),
                 ("en_lista", {"valores": ["queja", "PETICIÓN"]}, [T, T, F, T, T]),
                 ("no_en_lista", {"valores": ["queja", "PETICIÓN"]}, [F, F, T, F, F]),
                 ("parecido", {"valor": "petcion"}, [T, T, T, T, T]),
                 ("coincide", {"valor": "^\\s*petici[oó]n"}, [T, T, T, T, T]),
                 ("no_coincide", {"valor": "petición$"}, [F, F, T, F, T]),
                 ("no_vacio", {}, [T, T, T, T, T]),
                 ("vacio", {}, [F, F, F, F, F])]
        for op, extra, esperado in casos:
            for texto, e in zip(MIXTOS, esperado):
                with self.subTest(op=op, texto=texto):
                    self.assertEqual(bq.evaluar_valor(op, texto, {"operador": op, **extra}), e)
        # y de punta a punta en los dos nodos
        for tipo in ("condicion", "filtro"):
            for texto in MIXTOS:
                with self.subTest(tipo=tipo, texto=texto):
                    self.assertTrue(self.rama({"campo": "entrada.x", "operador": "contiene", "valor": "petición"}, {"x": texto}, tipo))
                    self.assertTrue(self.rama({"campo": "entrada.x", "operador": "palabra", "valor": "PETICION"}, {"x": texto}, tipo))
            self.assertFalse(self.rama({"campo": "entrada.x", "operador": "igual", "valor": "petición"}, {"x": "petición urgente"}, tipo))
        self.assertTrue(self.rama({"campo": "entrada.x", "operador": "vacio"}, {"x": "   "}))
        self.assertFalse(self.rama({"campo": "entrada.x", "operador": "palabra", "valor": "tutela"}, {"x": "fue tutelado"}))

    def test_regex_ignora_tildes_y_devuelve_el_original(self):
        texto = "La Señora PÉREZ NÚÑEZ presentó una PETICIÓN; radicádo 11001310300120240012300"
        d = manual(nodo("r", "extraer_regex", de="entrada.x", campos=[
            {"nombre": "quien", "patron": "senora (\\w+ \\w+)"},
            {"nombre": "tipo", "patron": "peticion"},
            {"nombre": "con_tildes", "patron": "peticion", "distinguir_tildes": True},
            {"nombre": "mayusculas", "patron": "petición", "distinguir_mayusculas": True},
            {"nombre": "radicado", "patron": "radicado:?\\s*(\\d{23})"},
            {"nombre": "verbo", "patron": "present[oó]"}]))
        self.assertEqual(self.problemas(d), [])
        r, _ = self.correr_orden(d, entrada={"x": texto})
        v = r["variables"]
        self.assertEqual((v["quien"], v["tipo"], v["con_tildes"], v["mayusculas"]), ("PÉREZ NÚÑEZ", "PETICIÓN", "", ""))
        self.assertEqual((v["radicado"], v["verbo"]), ("11001310300120240012300", "presentó"))
        # texto con tildes combinadas (NFD): lo encontrado sigue siendo el original completo
        nfd = "Señora Pérez"
        self.assertEqual(bq.Patron("senora (\\w+)").search(nfd).group(), nfd)
        self.assertEqual(bq.Patron("senora (\\w+)").search(nfd).group(1), "Pérez")
        self.assertEqual(bq.plegar_patron("[áa]ño\\é"), "[aa]noe")
        self.assertIsNotNone(bq.Patron("[áa]rbol").search("ÁRBOL"))
        # transformar: regex_extraer y regex_reemplazar sobre el original
        t = manual(nodo("t", "transformar", operaciones=[
            {"op": "regex_extraer", "de": "entrada.x", "patron": "senora (\\w+)", "salida": "apellido"},
            {"op": "regex_reemplazar", "de": "entrada.y", "patron": "peticion", "reemplazo": "[\\g<0>]", "salida": "marcado"},
            {"op": "regex_reemplazar", "de": "entrada.y", "patron": "(peticion)", "reemplazo": "«\\1»", "distinguir_tildes": True, "salida": "solo_sin"}]))
        self.assertEqual(self.problemas(t), [])
        r, _ = self.correr_orden(t, entrada={"x": texto, "y": "Una PETICIÓN, otra petición y una peticion"})
        self.assertEqual(r["variables"]["apellido"], "PÉREZ")
        self.assertEqual(r["variables"]["marcado"], "Una [PETICIÓN], otra [petición] y una [peticion]")
        self.assertEqual(r["variables"]["solo_sin"], "Una PETICIÓN, otra petición y una «peticion»")

    def test_nombre_de_adjunto_sin_tildes(self):
        for patron, leidos in (("CERTIFICÁDO", 1), ("^certificado\\.TXT$", 1), ("incapacidad", 0)):
            with self.subTest(patron=patron):
                d = cadena(nodo("adj", "leer_adjuntos", nombre_patron=patron, tipos=["texto"], ocr=False, salida="t"))
                d["nodos"][0]["config"]["leer_adjuntos"] = False
                r, _ = self.correr(d)
                self.assertEqual(bool(r["variables"].get("t")), bool(leidos), r["variables"].get("t"))

    def test_varias_condiciones_todas_alguna_y_varios_campos(self):
        conds = [{"campos": ["entrada.asunto", "entrada.cuerpo"], "operador": "contiene", "valor": "tutela"},
                 {"campo": "entrada.monto", "operador": "mayor", "valor": "1.000.000"}]
        base = {"asunto": "Acción de TUTÉLA", "cuerpo": "", "monto": "$ 1.500.000"}
        self.assertTrue(self.rama({"condiciones": conds}, base))
        self.assertFalse(self.rama({"condiciones": conds, "modo": "todas"}, {**base, "monto": "900.000"}))
        self.assertTrue(self.rama({"condiciones": conds, "modo": "alguna"}, {**base, "monto": "900.000"}))
        self.assertTrue(self.rama({"condiciones": conds}, {**base, "asunto": "Solicitud", "cuerpo": "anexo la tutela"}))  # basta un campo
        self.assertFalse(self.rama({"condiciones": conds, "modo": "alguna"}, {"asunto": "x", "cuerpo": "y", "monto": "10"}, "filtro"))
        # los negativos con varios campos tienen que cumplirse en todos
        self.assertFalse(self.rama({"campos": ["entrada.asunto", "entrada.cuerpo"], "operador": "no_contiene", "valor": "tutela"},
                                   {"asunto": "nada", "cuerpo": "una TUTELA"}))
        self.assertTrue(self.rama({"campos": ["entrada.asunto", "entrada.cuerpo"], "operador": "no_contiene", "valor": "tutela"},
                                  {"asunto": "nada", "cuerpo": "otra cosa"}))
        # validación: no se mezclan las dos formas; cada condición necesita campo; lista no vacía
        self.assertTrue(any("no las dos formas" in x for x in fd._validar_config(nodo("f", "filtro", campo="asunto", condiciones=conds))))
        self.assertTrue(any("falta campo" in x for x in fd._validar_config(nodo("f", "filtro", condiciones=[{"operador": "igual", "valor": "x"}]))))
        self.assertTrue(any("al menos una" in x for x in fd._validar_config(nodo("f", "condicion", condiciones=[]))))
        self.assertTrue(any("modo" in x for x in fd._validar_config(nodo("f", "condicion", condiciones=conds, modo="casi"))))
        # la forma de antes sigue igual
        self.assertEqual(fd._validar_config(nodo("f", "filtro", campo="asunto", operador="coincide", valor="@cendoj\\.ramajudicial\\.gov\\.co")), [])
        self.assertTrue(any("necesita campo" in x for x in fd._validar_config(nodo("f", "filtro", operador="contiene", valor="x"))))
        self.assertTrue(any("necesita valor" in x for x in fd._validar_config(nodo("f", "filtro", campo="asunto", operador="igual"))))
        # una variable que no existe en una condición se detecta
        d = desde_manual([nodo("c", "condicion", condiciones=[{"campo": "no_existe", "operador": "no_vacio"}]),
                          nodo("si", "salida", desenlace="manual"), nodo("no", "salida", desenlace="descartar")],
                         [{"desde": "inicio", "hacia": "c"}, {"desde": "c", "hacia": "si", "cuando": "si"}, {"desde": "c", "hacia": "no", "cuando": "no"}])
        self.assertProblema(d, "no_existe")

    def test_numeros_y_fechas_en_formatos_colombianos(self):
        for texto, n in (("1.234,56", 1234.56), ("$ 1,234.56", 1234.56), ("$1.500.000", 1500000.0), ("1,5", 1.5), ("-3", -3.0), ("12.345.678", 12345678.0),
                         ("COP 2.000", 2000.0), ("0,25 %", 0.25), (42, 42.0), ("sin número", None)):
            with self.subTest(texto=texto):
                self.assertEqual(bq.numero(texto), n)
        for texto in ("2026-11-07", "07/11/2026", "7-11-2026", "7 de noviembre de 2026", "Bogotá, noviembre 7 de 2026", "7 NOV 2026", "2026-11-07T10:30:00Z", "07.11.26"):
            with self.subTest(fecha=texto):
                self.assertEqual(bq.fecha(texto)[0].date().isoformat(), "2026-11-07")
        self.assertIsNone(bq.fecha("31/02/2026"))
        x = lambda v: {"x": v}  # noqa: E731
        self.assertTrue(self.rama({"campo": "entrada.x", "operador": "mayor", "valor": "1.000.000"}, x("$ 1,500,000.00")))
        self.assertFalse(self.rama({"campo": "entrada.x", "operador": "menor", "valor": 100}, x("100")))
        self.assertTrue(self.rama({"campo": "entrada.x", "operador": "menor_igual", "valor": 100}, x("100,0")))
        self.assertTrue(self.rama({"campo": "entrada.x", "operador": "mayor_igual", "valor": "99,5"}, x("Total: 99,50")))
        self.assertTrue(self.rama({"campo": "entrada.x", "operador": "entre", "valor": 1000, "hasta": "5.000"}, x("2.500")))
        self.assertFalse(self.rama({"campo": "entrada.x", "operador": "entre", "valor": [1000, 2000]}, x("2.500")))
        self.assertFalse(self.rama({"campo": "entrada.x", "operador": "mayor", "valor": 5}, x("no hay número")))
        self.assertTrue(self.rama({"campo": "entrada.x", "operador": "antes_de", "valor": "2026-12-01"}, x("7 de noviembre de 2026")))
        self.assertTrue(self.rama({"campo": "entrada.x", "operador": "despues_de", "valor": "01/11/2026"}, x("radicado el 7-11-2026")))
        self.assertTrue(self.rama({"campo": "entrada.x", "operador": "entre_fechas", "valor": "1 de noviembre de 2026", "hasta": "2026-11-30"}, x("07/11/2026")))
        self.assertFalse(self.rama({"campo": "entrada.x", "operador": "entre_fechas", "valor": ["2026-12-01", "2026-12-31"]}, x("07/11/2026")))
        self.assertTrue(self.rama({"campo": "entrada.x", "operador": "antes_de", "valor": "hoy"}, x("2020-01-01")))
        for conf, texto in (({"operador": "mayor", "valor": "mucho"}, "necesita un número"), ({"operador": "antes_de", "valor": "pronto"}, "necesita una fecha"),
                            ({"operador": "entre", "valor": 1}, "dos límites"), ({"operador": "entre_fechas", "valor": "ayer", "hasta": "nunca"}, "dos fechas"),
                            ({"operador": "longitud_igual", "valor": "diez"}, "un entero"), ({"operador": "teletransporta"}, "desconocido"),
                            ({"operador": "contiene_alguna"}, "necesita valores"), ({"operador": "coincide", "valor": "("}, "patrón inválido"),
                            ({"operador": "igual", "valor": "x", "comparacion": "casi"}, "comparacion"), ({"operador": "parecido", "valor": "x", "umbral": 2}, "umbral"),
                            ({"operador": "igual", "valor": "x", "distinguir_tildes": "si"}, "true o false")):
            with self.subTest(conf=conf):
                p = fd._validar_config(nodo("c", "condicion", campo="x", **conf))
                self.assertTrue(any(texto in m for m in p), p)
        self.assertEqual(fd._validar_config(nodo("c", "condicion", campo="x", operador="mayor", valor="{{limite}}")), [])  # plantilla: se ve al correr

    def test_parecida_exacta_y_opciones_por_condicion(self):
        x = lambda v: {"x": v}  # noqa: E731
        self.assertTrue(self.rama({"campo": "entrada.x", "operador": "parecido", "valor": "peticion urjente"}, x("Presento una Petición urgente, gracias")))
        self.assertFalse(self.rama({"campo": "entrada.x", "operador": "parecido", "valor": "peticion urjente", "umbral": 0.99}, x("Petición urgente")))
        self.assertTrue(self.rama({"campo": "entrada.x", "operador": "igual", "valor": "derecho de peticion", "comparacion": "parecida"}, x("Derecho de petcion")))
        self.assertFalse(self.rama({"campo": "entrada.x", "operador": "igual", "valor": "derecho de peticion"}, x("Derecho de petcion")))
        # exacta: mayúsculas, tildes y espacios cuentan
        self.assertTrue(self.rama({"campo": "entrada.x", "operador": "igual", "valor": "Petición", "comparacion": "exacta"}, x("Petición")))
        for otro in ("petición", "Peticion", "Petición ", "PETICIÓN"):
            with self.subTest(exacta=otro):
                self.assertFalse(self.rama({"campo": "entrada.x", "operador": "igual", "valor": "Petición", "comparacion": "exacta"}, x(otro)))
        self.assertFalse(self.rama({"campo": "entrada.x", "operador": "coincide", "valor": "peticion", "comparacion": "exacta"}, x("PETICIÓN")))
        # afinando la flexible
        o = {"campo": "entrada.x", "operador": "igual", "valor": "Petición"}
        self.assertFalse(self.rama({**o, "distinguir_mayusculas": True}, x("PETICIÓN")))
        self.assertTrue(self.rama({**o, "distinguir_mayusculas": True}, x("  Peticion ")))  # tildes y espacios siguen sin contar
        self.assertFalse(self.rama({**o, "distinguir_tildes": True}, x("peticion")))
        self.assertTrue(self.rama({**o, "distinguir_tildes": True}, x("PETICIÓN")))
        self.assertFalse(self.rama({"campo": "entrada.x", "operador": "igual", "valor": "petición urgente", "distinguir_espacios": True}, x("Petición  urgente")))
        self.assertTrue(self.rama({"campo": "entrada.x", "operador": "igual", "valor": "petición urgente"}, x("Petición  urgente")))
        self.assertEqual(bq.normalizar("  PETICIÓN  Urgente "), "peticion urgente")
        self.assertEqual(bq.normalizar("  PETICIÓN ", bq.opciones_de({"comparacion": "exacta"})), "  PETICIÓN ")

    def test_longitud_cedula_radicado_nit_y_telefono(self):
        x = lambda v: {"x": v}  # noqa: E731
        self.assertTrue(self.rama({"campo": "entrada.x", "operador": "longitud_igual", "valor": 10, "contar": "digitos"}, x("C.C. 1.023.456.789")))
        self.assertTrue(self.rama({"campo": "entrada.x", "operador": "longitud_entre", "valor": "6", "hasta": "10", "contar": "digitos"}, x("79.456.123")))
        self.assertFalse(self.rama({"campo": "entrada.x", "operador": "longitud_mayor", "valor": 3, "contar": "palabras"}, x("Ana  María Pérez")))
        self.assertTrue(self.rama({"campo": "entrada.x", "operador": "longitud_menor", "valor": 6, "contar": "letras"}, x("Ñ-12-ÁÉ")))
        self.assertEqual(bq.medir("  Ana   María ", "caracteres"), 9)
        self.assertEqual(bq.medir("  Ana   María ", "caracteres", bq.opciones_de({"comparacion": "exacta"})), 14)
        texto = ("Rad. 2024 del caso 11001310300120240012300. Cédula del trabajador: C.C. 1.023.456.789. "
                 "Empresa NIT 900.123.456-7. Teléfonos: 601 234 5678 y 300 123 4567; ext 12")
        d = manual(nodo("r", "extraer_regex", de="entrada.x", campos=[
            {"nombre": "radicado", "patron": "\\d+", "longitud": 23, "obligatorio": True},
            {"nombre": "cedula", "patron": "c\\.?c\\.?\\s*([\\d.]+)", "solo_digitos": True, "longitud_min": 6, "longitud_max": 10},
            {"nombre": "nit", "patron": "nit\\D{0,5}([\\d.\\-]+)", "solo_digitos": True, "longitud_min": 9, "longitud_max": 10},
            {"nombre": "telefonos", "patron": "\\d[\\d ]{8,}\\d", "solo_digitos": True, "longitud": 10, "todos": True},
            {"nombre": "corto", "patron": "\\d+", "longitud": 2},
            {"nombre": "imposible", "patron": "\\d+", "longitud": 40}]))
        self.assertEqual(self.problemas(d), [])
        r, ev = self.correr_orden(d, entrada={"x": texto})
        v = r["variables"]
        self.assertEqual((v["radicado"], v["cedula"], v["nit"]), ("11001310300120240012300", "1023456789", "9001234567"))
        self.assertEqual(v["telefonos"], ["6012345678", "3001234567"])
        self.assertEqual((v["corto"], v["imposible"]), ("12", ""))
        self.assertTrue(any("longitud_min" in m for m in fd._validar_config(nodo("r", "extraer_regex", campos=[
            {"nombre": "a", "patron": "\\d+", "longitud_min": 5, "longitud_max": 2}]))))
        self.assertTrue(any("contar" in m for m in fd._validar_config(nodo("r", "extraer_regex", campos=[{"nombre": "a", "patron": "\\d+", "contar": "bytes"}]))))
        # validar: longitud exacta o [min, max] con contar
        val = manual(nodo("v", "validar", reglas=[{"campo": "entrada.tel", "regla": "longitud", "valor": 10, "contar": "digitos", "codigo": "TEL"},
                                                  {"campo": "entrada.tema", "regla": "en_lista", "valor": ["Petición", "queja"]}]))
        self.assertEqual(self.problemas(val), [])
        reparos = lambda ev: next(e for e in ev if e.get("veredicto"))["reparos"]  # noqa: E731
        _, ev = self.correr_orden(val, entrada={"tel": "300 123 4567", "tema": "PETICION"})
        self.assertEqual(reparos(ev), [])
        r, ev = self.correr_orden(val, entrada={"tel": "300 123", "tema": "peticion"})
        self.assertEqual(([x["codigo"] for x in reparos(ev)], r["motivo"]), (["TEL"], "TEL"))
        _, ev = self.correr_orden(val, entrada={"tel": "3001234567", "tema": "reclamo"})
        self.assertEqual([x["codigo"] for x in reparos(ev)], ["EN_LISTA"])
        # transformar: recortar, rellenar, sin_tildes y regex_extraer con longitud
        t = manual(nodo("t", "transformar", operaciones=[
            {"op": "recortar", "de": "entrada.x", "largo": 8, "sufijo": "…", "salida": "corto"},
            {"op": "recortar", "de": "entrada.x", "largo": 2, "contar": "palabras", "salida": "dos"},
            {"op": "rellenar", "de": "entrada.n", "largo": 6, "salida": "ceros"},
            {"op": "rellenar", "de": "entrada.n", "largo": 6, "caracter": "*", "lado": "derecha", "salida": "estrellas"},
            {"op": "sin_tildes", "de": "entrada.x", "salida": "plano"},
            {"op": "regex_extraer", "de": "entrada.doc", "patron": "\\d[\\d.]*", "solo_digitos": True, "longitud_min": 6, "salida": "cedula"}]))
        self.assertEqual(self.problemas(t), [])
        r, _ = self.correr_orden(t, entrada={"x": "Señora Pérez Núñez", "n": "123", "doc": "año 2026, C.C. 1.023.456.789"})
        v = r["variables"]
        self.assertEqual((v["corto"], v["dos"], v["ceros"], v["estrellas"], v["plano"], v["cedula"]),
                         ("Señora P…", "Señora Pérez", "000123", "123***", "Senora Perez Nunez", "1023456789"))
        self.assertTrue(any("largo" in m for m in fd._validar_config(nodo("t", "transformar", op="rellenar", de="x", salida="y"))))

    def test_ia_extraer_respeta_la_longitud(self):
        self.modelo.r["campos"] = {"nombre": "Ana María Pérez", "documento": "C.C. 1.023.456.789", "telefono": "12345"}
        d = manual(nodo("ia", "ia_extraer", campos=[
            {"nombre": "nombre", "descripcion": "nombre"},
            {"nombre": "documento", "descripcion": "cédula", "solo_digitos": True, "longitud_min": 6, "longitud_max": 10, "obligatorio": True},
            {"nombre": "telefono", "descripcion": "celular", "solo_digitos": True, "longitud": 10, "contar": "digitos"}]))
        self.assertEqual(self.problemas(d), [])
        r, ev = self.correr_orden(d, entrada={})
        self.assertEqual((r["variables"]["documento"], r["variables"]["telefono"]), ("1023456789", ""))
        self.assertTrue(any("telefono" in a and "se descarta" in a for a in r.get("avisos") or []), r.get("avisos"))
        self.assertIn("solo dígitos, exactamente 10 dígitos", self.modelo.llamadas[-1]["sistema"])
        self.assertTrue(any("longitud_min" in m for m in fd._validar_config(nodo("ia", "ia_extraer", campos=[
            {"nombre": "a", "longitud_min": 9, "longitud_max": 3}]))))

    def test_tabla_consultar_normaliza_y_opera(self):
        t = fd.c_tablas
        t.guardar("flujo-prueba", "casos", "a", {"estado": "Pendiente", "monto": "1.500.000", "fecha": "7 de noviembre de 2026", "nombre": "José Pérez"})
        t.guardar("flujo-prueba", "casos", "b", {"estado": "PENDIENTE ", "monto": 900000, "fecha": "2026-12-15", "nombre": "Ana Núñez", "datos": {"ciudad": "Bogotá"}})
        t.guardar("flujo-prueba", "casos", "c", {"estado": "cerrado", "monto": "2,000,000.00", "fecha": "01/10/2026", "nombre": "jose perez"})
        claves = lambda filas: sorted(f["clave"] for f in filas)  # noqa: E731
        self.assertEqual(claves(t.consultar("flujo-prueba", "casos", filtro={"estado": "pendiente"})), ["a", "b"])
        self.assertEqual(claves(t.consultar("flujo-prueba", "casos", filtro={"monto": 900000})), ["b"])
        self.assertEqual(claves(t.consultar("flujo-prueba", "casos", filtro=[{"campo": "monto", "operador": "mayor", "valor": "1.000.000"}])), ["a", "c"])
        self.assertEqual(claves(t.consultar("flujo-prueba", "casos", filtro=[{"campo": "nombre", "operador": "contiene", "valor": "JOSE PEREZ"}])), ["a", "c"])
        self.assertEqual(claves(t.consultar("flujo-prueba", "casos", filtro=[{"campo": "nombre", "operador": "igual", "valor": "José Pérez", "comparacion": "exacta"}])), ["a"])
        self.assertEqual(claves(t.consultar("flujo-prueba", "casos", filtro=[{"campo": "fecha", "operador": "antes_de", "valor": "2026-12-01"}])), ["a", "c"])
        self.assertEqual(claves(t.consultar("flujo-prueba", "casos", filtro=[{"campo": "datos.ciudad", "operador": "igual", "valor": "bogota"}])), ["b"])
        alguna = [{"campo": "estado", "operador": "igual", "valor": "cerrado"}, {"campo": "monto", "operador": "menor", "valor": 1000000}]
        self.assertEqual(claves(t.consultar("flujo-prueba", "casos", filtro=alguna, modo="alguna")), ["b", "c"])
        self.assertEqual(claves(t.consultar("flujo-prueba", "casos", filtro=alguna)), [])
        self.assertEqual(len(t.consultar("flujo-prueba", "casos", filtro={"estado": "pendiente"}, limite=1)), 1)
        with self.assertRaises(fd.ErrorDeNegocio):
            t.consultar("flujo-prueba", "casos", filtro=[{"campo": "x') OR 1=1 --", "operador": "igual", "valor": 1}])
        # desde el nodo: filtro de iguales y condiciones juntos, con plantillas
        d = desde_manual([nodo("q", "tabla_consultar", tabla="casos", filtro={"estado": "{{entrada.estado}}"},
                               condiciones=[{"campos": ["nombre", "estado"], "operador": "parecido", "valor": "{{entrada.quien}}"}], salida="filas"),
                          nodo("si", "salida", desenlace="manual"), nodo("no", "salida", desenlace="descartar")],
                         [{"desde": "inicio", "hacia": "q"}, {"desde": "q", "hacia": "si", "cuando": "encontrado"}, {"desde": "q", "hacia": "no", "cuando": "vacio"}])
        self.assertEqual(self.problemas(d), [])
        r, _ = self.correr_orden(d, entrada={"estado": "PENDIENTE", "quien": "jose peres"})
        self.assertEqual([f["clave"] for f in r["variables"]["filas"]], ["a"])
        for conf, texto in (({"filtro": [{"campo": "a b", "operador": "igual", "valor": 1}]}, "inválido"), ({"filtro": []}, "vacía"),
                            ({"condiciones": [{"campo": "a", "operador": "mayor", "valor": "x"}]}, "número"), ({"modo": "casi", "condiciones": [{"campo": "a"}]}, "modo")):
            with self.subTest(conf=conf):
                p = fd._validar_config(nodo("q", "tabla_consultar", tabla="casos", **conf))
                self.assertTrue(any(texto in m for m in p), p)

    def test_buscar_en_tabla_y_bifurcar_con_comparacion(self):
        def bifurcar(valor, **conf):
            casos = ["peticion", "queja", "otro"]
            d = desde_manual([nodo("b", "bifurcar_por_valor", variable="entrada.x", casos=casos[:2], **conf)] +
                             [nodo(f"s_{x}", "salida", desenlace="manual", motivo=x) for x in casos],
                             [{"desde": "inicio", "hacia": "b"}] + [{"desde": "b", "hacia": f"s_{x}", "cuando": x} for x in casos])
            self.assertEqual(self.problemas(d), [])
            return self.correr_orden(d, entrada={"x": valor})[0]["motivo"]
        self.assertEqual(bifurcar("  PETICIÓN "), "peticion")
        self.assertEqual(bifurcar("PETICIÓN", comparacion="exacta"), "otro")
        self.assertEqual(bifurcar("peticon"), "otro")
        self.assertEqual(bifurcar("peticon", comparacion="parecida"), "peticion")
        self.assertEqual(bifurcar("felicitación", comparacion="parecida"), "otro")
        tabla = [{"tema": "Incapacidad", "area": "Prestaciones"}, {"tema": "Afiliación", "area": "Afiliaciones"}]
        def buscar(valor, **conf):
            d = desde_manual([nodo("t", "buscar_en_tabla", buscar="entrada.x", columna="tema", tabla=tabla, salida="fila", **conf),
                              nodo("si", "salida", desenlace="manual", motivo="{{fila.area}}"), nodo("no", "salida", desenlace="descartar", motivo="nada")],
                             [{"desde": "inicio", "hacia": "t"}, {"desde": "t", "hacia": "si", "cuando": "encontrado"}, {"desde": "t", "hacia": "no", "cuando": "no_encontrado"}])
            self.assertEqual(self.problemas(d), [])
            return self.correr_orden(d, entrada={"x": valor})[0]["motivo"]
        self.assertEqual(buscar(" AFILIACION "), "Afiliaciones")
        self.assertEqual(buscar("afiliacion", comparacion="exacta"), "nada")
        self.assertEqual(buscar("afilacion"), "nada")
        self.assertEqual(buscar("afilacion", comparacion="parecida"), "Afiliaciones")
        self.assertEqual(buscar("solicito pago de incapasidad", modo="contiene", comparacion="parecida"), "Prestaciones")
        self.assertTrue(any("umbral" in m for m in fd._validar_config(nodo("b", "bifurcar_por_valor", variable="x", casos=["a"], umbral=5))))

    def test_documentacion_y_esquema_de_busqueda(self):
        self.assertEqual(nd.OPERADORES, list(bq.OPERADORES))
        self.assertEqual(set(nd.OPERADORES), fd.OPERADORES)
        self.assertEqual(nd.CONTAR, list(bq.CONTAR))
        self.assertEqual(nd.COMPARACIONES, list(bq.COMPARACIONES))
        for op in bq.OPERADORES:
            self.assertIn(f"· {op}:", nd.DOCUMENTACION)
        for t in ("filtro", "condicion", "tabla_consultar"):
            props = nd.TIPOS[t]["config"]["properties"]
            self.assertEqual(props["condiciones"]["type"], "array")
            self.assertEqual(props["condiciones"]["items"]["title"], "Condición")  # el formulario muestra «Agregar condición»
            self.assertEqual(props["condiciones"]["items"]["properties"]["operador"]["enum"], nd.OPERADORES)
            self.assertEqual(props["modo"]["enum"], ["todas", "alguna"])
        self.assertNotIn("required", nd.TIPOS["filtro"]["config"])
        for texto in ("comparacion", "parecida", "distinguir_tildes", "solo_digitos", "longitud_min", "texto ORIGINAL"):
            self.assertIn(texto, nd.DOCUMENTACION)
        self.assertIn("busqueda", nd.esquema())

    def test_flujos_guardados_siguen_validos_y_corren(self):
        carpeta = Path(os.environ.get("RUTA_FLUJOS_GUARDADOS", "/datos/estado/flujos"))
        archivos = sorted(carpeta.glob("*.json")) if carpeta.is_dir() else []
        if not archivos:
            self.skipTest("no hay flujos guardados")
        for a in archivos:
            with self.subTest(flujo=a.name):
                j = json.loads(a.read_text(encoding="utf-8"))
                d = j.get("definicion", j)
                self.assertEqual(fd.validar(d), [])
                if d.get("id") == "solicitudes-de-incapacidad":
                    self.correr(d)  # corre de punta a punta sin error
        self.assertEqual(self.problemas(INCAPACIDAD), [])


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
                 {"tipo": "archivo_guardar", "config": {"ruta": "../x", "contenido": "x"}}, {"tipo": "expresion", "config": {}},
                 # búsqueda: formas mezcladas, operador o comparación desconocidos, condiciones sin campo o sin valores, rangos incompletos
                 {"tipo": "filtro", "config": {"campo": "asunto", "condiciones": [{"campo": "cuerpo"}]}},
                 {"tipo": "filtro", "config": {"campo": "asunto", "operador": "teletransporta"}},
                 {"tipo": "filtro", "config": {"operador": "contiene", "valor": "x"}},
                 {"tipo": "filtro", "config": {"campo": "asunto", "comparacion": "casi"}},
                 {"tipo": "filtro", "config": {"campo": "asunto", "operador": "igual"}},
                 {"tipo": "condicion", "config": {"condiciones": []}},
                 {"tipo": "condicion", "config": {"condiciones": [{"operador": "igual", "valor": "x"}]}},
                 {"tipo": "condicion", "config": {"campo": "monto", "operador": "entre", "valor": 1}},
                 {"tipo": "condicion", "config": {"campo": "x", "operador": "coincide", "valor": "("}},
                 {"tipo": "condicion", "config": {"condiciones": [{"campo": "x", "operador": "parecido", "valor": "y", "umbral": 3}], "modo": "todas"}},
                 {"tipo": "condicion", "config": {"condiciones": [{"campo": "x"}], "modo": "casi"}},
                 {"tipo": "extraer_regex", "config": {"campos": [{"nombre": "x", "patron": "\\d+", "longitud_min": 5, "longitud_max": 2}]}},
                 {"tipo": "extraer_regex", "config": {"campos": [{"nombre": "x", "patron": "\\d+", "contar": "bytes"}]}},
                 {"tipo": "transformar", "config": {"operaciones": [{"op": "rellenar", "de": "x", "salida": "y"}]}},
                 {"tipo": "transformar", "config": {"operaciones": [{"op": "rellenar", "de": "x", "salida": "y", "largo": 5, "lado": "centro"}]}},
                 {"tipo": "tabla_consultar", "config": {"tabla": "t", "condiciones": [{"campo": "a", "operador": "contiene_alguna"}]}},
                 {"tipo": "tabla_consultar", "config": {"tabla": "t", "filtro": [{"operador": "igual", "valor": 1}]}},
                 {"tipo": "tabla_consultar", "config": {"tabla": "t", "filtro": []}}]
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


class RedPrivadaDeCredenciales(unittest.TestCase):
    """Bases de datos y correo (credencial creada por una persona) pueden vivir en IP privadas; el resto no."""

    def test_reglas(self):
        from conectores.base import ip_bloqueada
        self.assertEqual(ip_bloqueada("10.20.0.5"), "es privada")
        self.assertIsNone(ip_bloqueada("10.20.0.5", red_privada=True))
        self.assertIsNone(ip_bloqueada("172.19.0.3", red_privada=True))
        self.assertIsNone(ip_bloqueada("192.168.1.10", red_privada=True))
        self.assertEqual(ip_bloqueada("127.0.0.1", red_privada=True), "es de loopback")
        self.assertIsNotNone(ip_bloqueada("169.254.169.254", red_privada=True))
        self.assertEqual(ip_bloqueada("169.254.10.1", red_privada=True), "es de enlace local")
        self.assertEqual(ip_bloqueada("::1", red_privada=True), "es de loopback")
        self.assertIsNone(ip_bloqueada("8.8.8.8"))

    def test_hosts_vetados_siguen_vetados(self):
        from conectores.base import destino_seguro, ErrorDeNegocio
        for h in ("metadata.google.internal", "localhost", "sgdea-prod.proyectos-3t.tech", "x.positiva.gov.co"):
            with self.assertRaises(ErrorDeNegocio):
                destino_seguro(h, 5432, red_privada=True)


# ------------------------------------------------- proveedores de IA (2c)
#
# Servidores falsos en 127.0.0.1 (solo aquí se permite loopback): uno con la API
# de chat de OpenAI y otro con la Messages API de Anthropic. La única llamada de
# verdad es a Vertex con la identidad del servicio (se salta si no hay ADC).

import proveedores as pv  # noqa: E402
from proveedores import base as pv_base  # noqa: E402
from proveedores import claude as pv_claude  # noqa: E402
from proveedores import estilo_openai as pv_openai  # noqa: E402

LLAVE_IA = "sk-prueba-LLAVE-MUY-SECRETA-1234567890"
LLAVE_CLAUDE = "sk-ant-prueba-OTRA-LLAVE-SECRETA-0987"


class _FalsoIA(http.server.BaseHTTPRequestHandler):
    """Responde como OpenAI (/v1/chat/completions, /v1/models) o como Anthropic (/v1/messages).

    `guion`: lista de respuestas que se toman en orden: (status, encabezados, cuerpo dict). Vacía: responde lo normal."""
    vistas: list = []
    guion: list = []
    demora = 0.0
    activos = 0
    maximo = 0
    cerrojo = threading.Lock()

    def log_message(self, *a):  # sin ruido en la consola
        pass

    def _responder(self, status, cuerpo, encabezados=None):
        datos = json.dumps(cuerpo).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        for k, v in (encabezados or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(datos)))
        self.end_headers()
        self.wfile.write(datos)

    def do_GET(self):
        _FalsoIA.vistas.append({"ruta": self.path, "auth": self.headers.get("Authorization"), "x-api-key": self.headers.get("x-api-key")})
        if self.path.startswith("/v1/models"):
            if "anthropic" in self.path or self.headers.get("x-api-key"):
                return self._responder(200, {"data": [{"id": "claude-haiku-5-5", "display_name": "Claude Haiku 5.5", "max_input_tokens": 1000000},
                                                      {"id": "claude-nuevo-9", "display_name": "Claude Nuevo"}]})
            return self._responder(200, {"data": [{"id": "gpt-4o-mini"}, {"id": "modelo-local-7b"}]})
        self._responder(404, {"error": "no"})

    def do_POST(self):
        cuerpo = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        with _FalsoIA.cerrojo:
            _FalsoIA.activos += 1
            _FalsoIA.maximo = max(_FalsoIA.maximo, _FalsoIA.activos)
            _FalsoIA.vistas.append({"ruta": self.path, "cuerpo": cuerpo, "auth": self.headers.get("Authorization"),
                                    "x-api-key": self.headers.get("x-api-key"), "version": self.headers.get("anthropic-version"),
                                    "x-title": self.headers.get("X-Title")})
            paso = _FalsoIA.guion.pop(0) if _FalsoIA.guion else None
        try:
            if _FalsoIA.demora:
                time.sleep(_FalsoIA.demora)
            if paso:
                return self._responder(paso[0], paso[2], paso[1])
            if self.path.startswith("/v1/messages"):
                return self._responder(200, self._anthropic(cuerpo))
            return self._responder(200, self._openai(cuerpo))
        finally:
            with _FalsoIA.cerrojo:
                _FalsoIA.activos -= 1

    @staticmethod
    def _openai(c):
        uso = {"prompt_tokens": 100, "completion_tokens": 20}
        msgs = c.get("messages") or []
        if c.get("tools") and not any(m.get("role") == "tool" for m in msgs):
            return {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "sumar", "arguments": json.dumps({"a": 2, "b": 3})}}]},
                "finish_reason": "tool_calls"}], "usage": uso}
        if any(m.get("role") == "tool" for m in msgs):
            res = next(m["content"] for m in msgs if m.get("role") == "tool")
            return {"choices": [{"message": {"role": "assistant", "content": f"El resultado es {res}"}, "finish_reason": "stop"}], "usage": uso}
        sistema = (msgs[0].get("content") if msgs and msgs[0].get("role") == "system" else "") or ""
        if c.get("response_format") or "JSON" in sistema:
            return {"choices": [{"message": {"role": "assistant", "content": json.dumps({"categoria": "incapacidad", "confianza": 0.9, "evidencia": "pago"})},
                                 "finish_reason": "stop"}], "usage": uso}
        return {"choices": [{"message": {"role": "assistant", "content": "hola desde el falso"}, "finish_reason": "stop"}], "usage": uso}

    @staticmethod
    def _anthropic(c):
        uso = {"input_tokens": 50, "output_tokens": 10}
        msgs = c.get("messages") or []
        tiene_resultado = any(isinstance(b, dict) and b.get("type") == "tool_result" for m in msgs for b in (m.get("content") if isinstance(m.get("content"), list) else []))
        if c.get("tools") and not tiene_resultado:
            return {"content": [{"type": "thinking", "thinking": "", "signature": "firma"}, {"type": "tool_use", "id": "toolu_1", "name": "sumar", "input": {"a": 4, "b": 5}}],
                    "stop_reason": "tool_use", "usage": uso}
        if tiene_resultado:
            return {"content": [{"type": "text", "text": "Listo: 9"}], "stop_reason": "end_turn", "usage": uso}
        if c.get("output_config"):
            return {"content": [{"type": "text", "text": json.dumps({"categoria": "otro", "confianza": 0.7, "evidencia": "x"})}], "stop_reason": "end_turn", "usage": uso}
        return {"content": [{"type": "text", "text": "hola, soy Claude falso"}], "stop_reason": "end_turn", "usage": uso}


ESQUEMA_CAT = {"type": "object", "properties": {"categoria": {"type": "string", "enum": ["incapacidad", "otro"]}, "confianza": {"type": "number"},
                                                "evidencia": {"type": "string"}}, "required": ["categoria", "confianza"]}


class ProveedoresDeIA(Base):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FalsoIA)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        super().setUp()
        fd._llamar_modelo = self._orig[2]  # la capa de proveedores de verdad, contra los servidores falsos
        fd._IPS_PERMITIDAS_PRUEBAS.add("127.0.0.1")
        _FalsoIA.vistas, _FalsoIA.guion, _FalsoIA.demora, _FalsoIA.maximo, _FalsoIA.activos = [], [], 0.0, 0, 0
        self.dormidas = []
        self._dormir_pv, self._reloj_pv = pv_base.dormir, pv_base.reloj
        pv_base.dormir = self.dormidas.append
        pv_base.reiniciar_limites()
        self._urls = dict(pv_openai.URLS), dict(pv_claude.URL)
        pv_openai.URLS["openai"] = f"{self.url}/v1"
        pv_claude.URL["base"] = self.url
        self.cred = {"base_url": f"{self.url}/v1", "api_key": LLAVE_IA}

    def tearDown(self):
        pv_base.dormir, pv_base.reloj = self._dormir_pv, self._reloj_pv
        pv_openai.URLS.clear()
        pv_openai.URLS.update(self._urls[0])
        pv_claude.URL.update(self._urls[1])
        pv_base.reiniciar_limites()
        super().tearDown()

    # ------------------------------------------------------------ texto y JSON
    def test_texto_compatible(self):
        r = pv.generar("openai_compatible", "modelo-local-7b", "Eres breve.", tarea="Hola", temperatura=0.2, credencial=self.cred)
        self.assertEqual(r["texto"], "hola desde el falso")
        self.assertEqual((r["tokens_entrada"], r["tokens_salida"], r["proveedor"]), (100, 20, "openai_compatible"))
        self.assertFalse(r["costo_conocido"])  # un modelo propio no tiene precio en el catálogo
        v = _FalsoIA.vistas[-1]
        self.assertEqual(v["auth"], f"Bearer {LLAVE_IA}")
        self.assertEqual(v["cuerpo"]["messages"][0], {"role": "system", "content": "Eres breve."})
        self.assertEqual(v["cuerpo"]["temperature"], 0.2)

    def test_json_nativo_en_openai_con_costo(self):
        r = pv.generar("openai", "gpt-4o-mini", "Clasifica.", tarea="Pago de incapacidad", esquema_json=ESQUEMA_CAT, temperatura=0.1,
                       credencial={"api_key": LLAVE_IA})
        self.assertEqual(r["json"]["categoria"], "incapacidad")
        formato = _FalsoIA.vistas[-1]["cuerpo"]["response_format"]
        self.assertEqual(formato["type"], "json_schema")
        self.assertFalse(formato["json_schema"]["schema"]["additionalProperties"])  # OpenAI lo exige
        self.assertTrue(r["costo_conocido"])
        self.assertAlmostEqual(r["costo_estimado_usd"], (100 * 0.15 + 20 * 0.60) / 1e6, places=9)

    def test_json_instruido_se_valida_y_se_repara_una_vez(self):
        _FalsoIA.guion = [(200, {}, {"choices": [{"message": {"content": "no sé, quizá incapacidad"}}], "usage": {"prompt_tokens": 5, "completion_tokens": 5}})]
        r = pv.generar("openai_compatible", "modelo-local-7b", "Clasifica.", tarea="x", esquema_json=ESQUEMA_CAT, credencial=self.cred)
        self.assertTrue(r["reparado"])
        self.assertEqual(r["errores_json"], [])
        self.assertEqual(r["json"]["categoria"], "incapacidad")
        self.assertEqual(r["tokens_entrada"], 105)  # suma las dos llamadas
        self.assertIn("JSON Schema", _FalsoIA.vistas[0]["cuerpo"]["messages"][0]["content"])  # instruido: el esquema va en el sistema
        self.assertIn("no sirve", json.dumps(_FalsoIA.vistas[1]["cuerpo"]["messages"], ensure_ascii=False))

    # -------------------------------------------------------- reintentos y límites
    def test_reintenta_429_respetando_retry_after(self):
        _FalsoIA.guion = [(429, {"Retry-After": "2"}, {"error": {"message": "despacio"}})]
        r = pv.generar("openai_compatible", "m", "", tarea="hola", credencial=self.cred)
        self.assertEqual(r["intentos"], 2)
        self.assertEqual(len(self.dormidas), 1)
        self.assertTrue(2.0 <= self.dormidas[0] <= 2.25, self.dormidas)

    def test_5xx_se_agota_y_no_reintenta_401(self):
        _FalsoIA.guion = [(503, {}, {"error": "caído"})] * 3
        with self.assertRaises(pv.ErrorDeProveedor) as e:
            pv.generar("openai_compatible", "m", "", tarea="hola", credencial=self.cred)
        self.assertEqual(e.exception.status_code, 503)
        self.assertEqual(len(_FalsoIA.vistas), 3)
        self.assertTrue(fd.es_transitorio(e.exception))
        _FalsoIA.vistas, _FalsoIA.guion = [], [(401, {}, {"error": {"message": "llave mala"}})]
        with self.assertRaises(pv.ErrorDeProveedor):
            pv.generar("openai_compatible", "m", "", tarea="hola", credencial=self.cred)
        self.assertEqual(len(_FalsoIA.vistas), 1)

    def test_balde_de_tasa_por_flujo(self):
        t = [0.0]
        pv_base.reloj = lambda: t[0]
        pv_base.dormir = lambda s: (self.dormidas.append(round(s, 3)), t.__setitem__(0, t[0] + s))
        b = pv_base.balde("openai", "huella", "flujo-a", por_minuto=6)  # una cada 10 s, ráfaga de 1
        for _ in range(3):
            b.tomar(30)
        self.assertEqual(self.dormidas, [10.0, 10.0])
        # el balde de otro flujo está lleno: la ráfaga de flujo-a no lo frena
        pv_base.balde("openai", "huella", "flujo-b", por_minuto=6).tomar(0)
        self.assertEqual(self.dormidas, [10.0, 10.0])
        with self.assertRaises(pv_base.ErrorTransitorio):
            b.tomar(0.1)  # haría falta esperar más del tope

    def test_semaforo_por_proveedor_y_credencial(self):
        os.environ["IA_CONCURRENCIA_OPENAI_COMPATIBLE"] = "2"
        _FalsoIA.demora = 0.3
        try:
            hilos = [threading.Thread(target=pv.generar, args=("openai_compatible", "m", "", None, "hola"), kwargs={"credencial": self.cred})
                     for _ in range(5)]
            for h in hilos:
                h.start()
            for h in hilos:
                h.join(30)
        finally:
            os.environ.pop("IA_CONCURRENCIA_OPENAI_COMPATIBLE", None)
        self.assertEqual(len(_FalsoIA.vistas), 5)
        self.assertEqual(_FalsoIA.maximo, 2)

    # ------------------------------------------------------------------ máscara
    def test_la_llave_nunca_sale(self):
        _FalsoIA.guion = [(401, {}, {"error": {"message": f"Incorrect API key provided: {LLAVE_IA}"}})]
        with self.assertRaises(pv.ErrorDeProveedor) as e:
            pv.generar("openai_compatible", "m", "", tarea="hola", credencial=self.cred)
        self.assertNotIn(LLAVE_IA, str(e.exception))
        self.assertIn("••••", str(e.exception))
        r = pv.probar("anthropic", "claude-haiku-5-5", {"api_key": LLAVE_CLAUDE})
        self.assertTrue(r["ok"], r)
        self.assertNotIn(LLAVE_CLAUDE, json.dumps(r))
        self.assertEqual(pv.enmascarar(f"key={LLAVE_IA} y AIzaSyA1234567890abcdefghijklmn"), "key=•••• y ••••")
        # el CLI del servidor: una URL vetada (metadatos) falla sin red y sin dejar salir la llave
        p = subprocess.run([sys.executable, str(AQUI / "proveedores" / "cli.py"), "probar"],
                           input=json.dumps({"proveedor": "openai_compatible", "modelo": "m", "credencial": {"base_url": "http://169.254.169.254/v1", "api_key": LLAVE_IA}}),
                           capture_output=True, text=True, encoding="utf-8", timeout=60, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        r = json.loads(p.stdout.strip().splitlines()[-1])
        self.assertFalse(r["ok"])
        self.assertIn("bloqueado", r["error"])
        self.assertNotIn(LLAVE_IA, p.stdout + p.stderr)

    # ----------------------------------------------------- Claude y herramientas
    def test_anthropic_texto_json_y_modelos(self):
        r = pv.generar("anthropic", "claude-haiku-5-5", "Clasifica.", tarea="x", esquema_json=ESQUEMA_CAT, temperatura=0.3, credencial={"api_key": LLAVE_CLAUDE})
        self.assertEqual(r["json"]["categoria"], "otro")
        v = _FalsoIA.vistas[-1]
        self.assertEqual((v["x-api-key"], v["version"]), (LLAVE_CLAUDE, pv_claude.VERSION))
        self.assertEqual(v["cuerpo"]["output_config"]["format"]["type"], "json_schema")
        self.assertNotIn("temperature", v["cuerpo"])  # los modelos actuales la rechazan
        self.assertEqual(r["costo_estimado_usd"], round((50 * 0.10 + 10 * 0.50) / 1e6, 6))
        vivos = pv.catalogo.en_vivo("anthropic", {"api_key": LLAVE_CLAUDE})
        self.assertEqual({m["id"] for m in vivos}, {"claude-haiku-5-5", "claude-nuevo-9"})
        self.assertTrue(next(m for m in vivos if m["id"] == "claude-haiku-5-5")["acepta"]["imagenes"])  # lo conocido trae su ficha

    def test_conversar_con_herramientas_en_claude_y_estilo_openai(self):
        sumar = {"nombre": "sumar", "descripcion": "suma dos números", "parametros": {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}}}
        avisos = []
        r = pv.conversar_con_herramientas("anthropic", "claude-haiku-5-5", {"api_key": LLAVE_CLAUDE}, "Usa herramientas.",
                                          [{"rol": "usuario", "texto": "¿4+5?"}], [sumar], lambda n, a: {"total": a["a"] + a["b"]},
                                          aviso=lambda t, **d: avisos.append(t))
        self.assertEqual(r["texto"], "Listo: 9")
        self.assertEqual(r["pasos"][0]["resultado"], {"total": 9})
        segunda = _FalsoIA.vistas[-1]["cuerpo"]["messages"]
        self.assertEqual(segunda[1]["content"][0]["type"], "thinking")  # el contenido del asistente vuelve tal cual
        self.assertEqual(segunda[2]["content"][0]["type"], "tool_result")
        self.assertIn("herramienta_fin", avisos)
        r = pv.conversar_con_herramientas("openai_compatible", "m", self.cred, "", [{"rol": "usuario", "texto": "¿2+3?"}], [sumar],
                                          lambda n, a: a["a"] + a["b"])
        self.assertEqual(r["texto"], "El resultado es 5")
        self.assertEqual(r["vueltas"], 2)
        self.assertEqual(r["tokens_entrada"], 200)

    def test_personalizado_sin_codigo(self):
        cred = {"url": f"{self.url}/v1/chat/completions", "api_key": LLAVE_IA, "encabezados": json.dumps({"Authorization": "Token {{api_key}}"}),
                "cuerpo": json.dumps({"model": "{{modelo}}", "messages": "{{mensajes_con_sistema}}", "temperature": "{{temperatura}}"}),
                "ruta_texto": "choices.0.message.content", "ruta_tokens_entrada": "usage.prompt_tokens", "ruta_tokens_salida": "usage.completion_tokens"}
        r = pv.generar("personalizado", "mi-modelo", "Sé breve.", tarea="hola", temperatura=0.5, credencial=cred)
        self.assertEqual((r["texto"], r["tokens_entrada"]), ("hola desde el falso", 100))
        v = _FalsoIA.vistas[-1]
        self.assertEqual(v["auth"], f"Token {LLAVE_IA}")
        self.assertEqual(v["cuerpo"]["temperature"], 0.5)
        self.assertEqual(v["cuerpo"]["messages"][1], {"role": "user", "content": "hola"})

    def test_partes_que_el_modelo_no_acepta(self):
        with self.assertRaises(fd.ErrorDeNegocio) as e:
            pv.generar("deepseek", "deepseek-chat", "", tarea="lee", partes=[{"tipo": "imagen", "datos": b"\xff\xd8", "mime": "image/jpeg"}],
                       credencial={"api_key": LLAVE_IA})
        self.assertIn("no acepta imágenes", str(e.exception))
        with self.assertRaises(fd.ErrorDeNegocio):
            pv.generar("vertex", "gemini-3.8-live", "", tarea="hola")  # solo voz

    # --------------------------------------------------------- resolución y validación
    def test_resolucion_nodo_flujo_global(self):
        g = {"proveedor": "openai", "modelo": "gpt-4o-mini", "credencial": "ia-openai"}
        self.assertEqual(pv.ia_del_flujo({"modelo": "gemini-2.5-pro"}, g)["proveedor"], "vertex")  # los de antes siguen en Vertex
        self.assertEqual(pv.ia_del_flujo({"modelo": "gemini-2.5-pro"}, g)["modelo"], "gemini-2.5-pro")
        f = pv.ia_del_flujo({"ia": {"usar": "global"}}, g)
        self.assertEqual((f["proveedor"], f["modelo"], f["origen"]), ("openai", "gpt-4o-mini", "global"))
        self.assertEqual(pv.ia_del_flujo({"ia": {"usar": "global"}}, None)["proveedor"], "vertex")
        propio = pv.ia_del_flujo({"ia": {"proveedor": "anthropic", "modelo": "claude-haiku-5-5", "credencial": "c"}}, g)
        self.assertEqual(propio["origen"], "flujo")
        n = pv.ia_del_nodo({"proveedor": "deepseek", "modelo": "deepseek-chat", "credencial": "ds"}, propio)
        self.assertEqual((n["proveedor"], n["credencial"], n["origen"]), ("deepseek", "ds", "nodo"))
        n = pv.ia_del_nodo({"modelo": "claude-sonnet-5-5"}, propio)
        self.assertEqual((n["proveedor"], n["modelo"], n["credencial"]), ("anthropic", "claude-sonnet-5-5", "c"))

    def test_validacion_de_la_ia(self):
        creds = {"ia-ds": {"tipo": "ia_deepseek", "alcance": "global"}, "ia-otro": {"tipo": "ia_openai", "alcance": "otro-flujo"},
                 "buzon": {"tipo": "imap", "alcance": "global"}}
        base = cadena(nodo("tema", "ia_clasificar", categorias=["a", "b"], salida="tema"))

        def con(ia=None, **conf):
            d = copy.deepcopy(base)
            if ia is not None:
                d["ia"] = ia
            d["nodos"][1]["config"].update(conf)
            return fd.validar(d, credenciales=creds)

        self.assertEqual([p for p in con({"usar": "global"}) if "ia" in p.lower()], [])
        self.assertTrue(any("desconocido" in p for p in con({"proveedor": "nadie", "modelo": "x"})))
        self.assertTrue(any("necesita credencial" in p for p in con({"proveedor": "deepseek", "modelo": "deepseek-chat"})))
        self.assertTrue(any("es de tipo imap" in p for p in con({"proveedor": "deepseek", "modelo": "deepseek-chat", "credencial": "buzon"})))
        self.assertTrue(any("no existe" in p for p in con({"proveedor": "deepseek", "modelo": "deepseek-chat", "credencial": "no-esta"})))
        self.assertTrue(any("del flujo «otro-flujo»" in p for p in con({"proveedor": "openai", "modelo": "gpt-4o", "credencial": "ia-otro"})))
        self.assertTrue(any("voz en vivo" in p for p in con({"proveedor": "vertex", "modelo": "gemini-3.8-live"})))
        self.assertEqual([p for p in con({"proveedor": "deepseek", "modelo": "deepseek-chat", "credencial": "ia-ds"}) if "ia" in p.lower()], [])
        # por nodo: proveedor sin modelo, y visión que el modelo no tiene
        self.assertTrue(any("hace falta también el modelo" in p for p in con(None, proveedor="openai")))
        d = cadena(nodo("adj", "leer_adjuntos", vision=True), fin=nodo("fin", "salida", desenlace="manual"))
        d["ia"] = {"proveedor": "deepseek", "modelo": "deepseek-chat", "credencial": "ia-ds"}
        self.assertTrue(any("no acepta imágenes" in p for p in fd.validar(d, credenciales=creds)))
        d["nodos"][1]["config"].pop("vision")  # sin pedirla explícitamente: no bloquea (se avisa al correr y queda solo OCR)
        self.assertFalse(any("no acepta" in p for p in fd.validar(d, credenciales=creds)))
        # la forma también la revisa la biblioteca de nodos: los comunes de IA están en el esquema de cada nodo de IA
        props = nd.TIPOS["ia_extraer"]["config"]["properties"]
        self.assertTrue(all(k in props and props[k].get("x-comun") for k in ("proveedor", "modelo", "credencial")))
        self.assertEqual(nd.PROVEEDORES_IA, list(pv.PROVEEDORES))

    # ------------------------------------------- un flujo de verdad con dos proveedores
    def test_flujo_con_proveedor_propio_override_y_consumo(self):
        d = cadena(nodo("tema", "ia_clasificar", categorias=["incapacidad", "otro"], salida="tema"),
                   nodo("resumen", "resumir", proveedor="anthropic", modelo="claude-haiku-5-5", credencial="ia-claude", salida="resumen"))
        d["ia"] = {"proveedor": "openai_compatible", "modelo": "modelo-local-7b", "credencial": "ia-local"}
        creds = {"ia-local": cred("ia_compatible", secretos=["api_key"], base_url=f"{self.url}/v1", api_key=LLAVE_IA),
                 "ia-claude": cred("ia_anthropic", secretos=["api_key"], api_key=LLAVE_CLAUDE)}
        self.salida.seek(0)
        self.salida.truncate()
        asyncio.run(fd.procesar({"cmd": "procesar", "corrida": "x", "caso": CASO, "definicion": d, "credenciales": creds,
                                 "ia_global": {"proveedor": "vertex", "modelo": "gemini-2.5-flash"}}))
        eventos = [json.loads(x[len(fd.MARCA):]) for x in self.salida.getvalue().split(SALTO) if x.startswith(fd.MARCA)]
        r = next(e for e in eventos if e["tipo"] == "resultado")
        self.assertNotEqual(r["accion"], "error", r.get("motivo"))
        self.assertEqual(r["variables"]["tema"], "incapacidad")
        self.assertEqual(r["variables"]["resumen"], "hola, soy Claude falso")
        fines = [e for e in eventos if e["tipo"] == "modelo_fin"]
        self.assertEqual([f["proveedor"] for f in fines], ["openai_compatible", "anthropic"])
        self.assertTrue(all("costo_estimado_usd" in f and "tokens_entrada" in f for f in fines))
        c = r["consumo"]
        self.assertEqual((c["tokens_entrada"], c["tokens_salida"], c["llamadas"]), (150, 30, 2))
        self.assertEqual(set(c["por_proveedor"]), {"openai_compatible", "anthropic"})
        self.assertEqual(c["por_proveedor"]["anthropic"]["modelos"], {"claude-haiku-5-5": 1})
        self.assertEqual(r["ia"]["origen"], "flujo")
        texto = self.salida.getvalue()
        self.assertNotIn(LLAVE_IA, texto)
        self.assertNotIn(LLAVE_CLAUDE, texto)
        # sin la credencial (otro flujo no la recibe), la corrida no arranca: la validación lo dice
        asyncio.run(fd.procesar({"cmd": "procesar", "corrida": "y", "caso": CASO, "definicion": d, "credenciales": {}}))
        r = [json.loads(x[len(fd.MARCA):]) for x in self.salida.getvalue().split(SALTO) if x.startswith(fd.MARCA)][-1]
        self.assertEqual(r["accion"], "error")
        self.assertTrue(any("no existe o este flujo no la puede usar" in a for a in r["avisos"]), r["avisos"])

    # ------------------------------------------------------------- Vertex de verdad
    def test_vertex_de_verdad(self):
        if os.environ.get("SIN_VERTEX"):
            self.skipTest("SIN_VERTEX")
        try:
            import google.auth

            google.auth.default()
        except Exception:
            self.skipTest("sin identidad de Google (ADC)")
        pv_base.dormir = time.sleep
        r = pv.generar("vertex", "gemini-2.5-flash", "Clasifica el texto.", tarea="Solicito el pago de mi incapacidad", esquema_json=ESQUEMA_CAT,
                       temperatura=0.0, flujo="prueba")
        self.assertEqual(r["json"]["categoria"], "incapacidad")
        self.assertGreater(r["tokens_entrada"], 0)
        self.assertGreater(r["costo_estimado_usd"], 0)


class ParidadDeProveedores(unittest.TestCase):
    """Los proveedores y sus tipos de credencial son los mismos en Python, en la biblioteca de nodos y en el servidor."""

    def test_listas(self):
        import shutil

        self.assertEqual(nd.PROVEEDORES_IA, list(pv.PROVEEDORES))
        tipos = {a.id: a.tipo_credencial for a in pv.PROVEEDORES.values()}
        self.assertTrue({t for t in tipos.values() if t} <= set(nd.TIPOS_CREDENCIAL_IA))
        dist = Path(os.environ.get("RUTA_SERVIDOR_DIST", "/app/servidor/dist")) / "ia.js"
        node = shutil.which("node")
        if not node or not dist.exists():
            self.skipTest("no hay servidor compilado o node")
        guion = "const f = await import(process.argv[1]); console.log(JSON.stringify({p: [...f.PROVEEDORES_IA], c: f.CREDENCIAL_DE}));"
        p = subprocess.run([node, "--input-type=module", "-e", guion, dist.as_uri()], capture_output=True, text=True, encoding="utf-8", timeout=60)
        r = json.loads(p.stdout.strip().splitlines()[-1])
        self.assertEqual(r["p"], list(pv.PROVEEDORES))
        self.assertEqual(r["c"], tipos)


# =====================================================================
#   Cada flujo independiente: archivos subidos, su bandeja propia y su buzón
# =====================================================================

class ArchivosDelFlujo(Plataforma):
    """Los archivos subidos a una corrida se leen como adjuntos y solo del espacio del propio flujo."""

    def subir(self, nombre, datos, flujo="prueba", aid=None, tipo="text/plain"):
        aid = aid or f"a-{abs(hash(nombre)) % 10 ** 12:012d}"
        carpeta = self.archivos / flujo / "entradas" / aid
        carpeta.mkdir(parents=True, exist_ok=True)
        (carpeta / nombre).write_bytes(datos)
        return {"id": aid, "nombre": nombre, "tipo": tipo, "tamano": len(datos), "sha256": "x", "ruta": str(carpeta / nombre)}

    def test_entrada_archivos_llega_a_leer_adjuntos_con_filtros(self):
        a = self.subir("CERTIFICACIÓN médica.txt", CERTIFICADO.encode("utf-8"))
        b = self.subir("otra-cosa.txt", "texto que no se lee".encode("utf-8"))
        d = flujo([nodo("archivos", "entrada_archivos", leer=False),
                   nodo("leer", "leer_adjuntos", nombre_patron="certificacion", salida="texto_cert"),
                   nodo("fin", "salida", desenlace="manual", motivo="{{archivos.cantidad}}|{{archivos.nombres}}|{{archivos.leidos}}")],
                  [{"desde": "archivos", "hacia": "leer"}, {"desde": "leer", "hacia": "fin"}])
        self.assertEqual(fd.validar(d), [])
        r, eventos = self.correr_orden(d, archivos=[a, b], entrada={"archivos": [a["id"], b["id"]]})
        self.assertEqual(r["motivo"], "2|CERTIFICACIÓN médica.txt, otra-cosa.txt|CERTIFICACIÓN médica.txt")
        self.assertIn("Ana María Pérez", r["variables"]["texto_cert"])  # se leyó con la conversión del motor
        self.assertTrue(any(e.get("titulo", "").startswith("otra-cosa.txt: se omite") for e in eventos))
        self.assertEqual([x["nombre"] for x in r["variables"]["entrada"]["archivos"]], ["CERTIFICACIÓN médica.txt", "otra-cosa.txt"])

    def test_entrada_archivos_lee_sola_y_exige_archivos(self):
        a = self.subir("acta.txt", CERTIFICADO.encode("utf-8"))
        d = flujo([nodo("archivos", "entrada_archivos"), nodo("fin", "salida", desenlace="manual", motivo="{{archivos.texto}}")],
                  [{"desde": "archivos", "hacia": "fin"}])
        r, _ = self.correr_orden(d, archivos=[a])
        self.assertIn("Ana María Pérez", r["motivo"])
        r, _ = self.correr_orden(d, exigir_ok=False, archivos=[])
        self.assertEqual(r["accion"], "error")
        self.assertIn("no trae archivos", r["motivo"])

    def test_archivos_de_otro_flujo_o_video_no_entran(self):
        ajeno = self.subir("ajeno.txt", b"de otro flujo", flujo="otro-flujo")
        video = self.subir("clase.mp4", b"\x00\x00\x00\x18ftypisom", tipo="video/mp4")
        bueno = self.subir("bueno.txt", CERTIFICADO.encode("utf-8"))
        d = flujo([nodo("archivos", "entrada_archivos"), nodo("fin", "salida", desenlace="manual", motivo="{{archivos.nombres}}")],
                  [{"desde": "archivos", "hacia": "fin"}])
        r, _ = self.correr_orden(d, archivos=[ajeno, video, bueno])
        self.assertEqual(r["motivo"], "bueno.txt")
        self.assertTrue(any("no está en el espacio de este flujo" in x for x in r["avisos"]))
        self.assertTrue(any("video o audio" in x for x in r["avisos"]))

    def test_tipos_permitidos_y_tamano(self):
        self.assertProblema(flujo([nodo("a", "entrada_archivos", tipos_permitidos=["pdf", "mp4"]), nodo("fin", "salida", desenlace="manual")],
                                  [{"desde": "a", "hacia": "fin"}]), "video o audio")
        a = self.subir("foto.png", b"\x89PNG\r\n\x1a\n", tipo="image/png")
        d = flujo([nodo("a", "entrada_archivos", tipos_permitidos=["pdf"], leer=False), nodo("fin", "salida", desenlace="manual")], [{"desde": "a", "hacia": "fin"}])
        r, _ = self.correr_orden(d, exigir_ok=False, archivos=[a])
        self.assertIn("no acepta archivos .png", r["motivo"])

    def test_campo_archivo_en_entrada_manual(self):
        a = self.subir("soporte.txt", CERTIFICADO.encode("utf-8"))
        d = manual(nodo("leer", "leer_adjuntos", salida="soporte_texto"), campos=[{"nombre": "soporte", "tipo": "archivo", "obligatorio": True}],
                   fin=nodo("fin", "salida", desenlace="manual", motivo="{{entrada.soporte.nombre}}"))
        self.assertEqual(fd.validar(d), [])
        r, _ = self.correr_orden(d, archivos=[a], entrada={"soporte": a["id"]})
        self.assertEqual(r["motivo"], "soporte.txt")
        self.assertIn("Ana María Pérez", r["variables"]["soporte_texto"])
        r, _ = self.correr_orden(d, exigir_ok=False, archivos=[], entrada={})
        self.assertIn("faltan en la entrada: soporte", r["motivo"])

    def test_webhook_con_archivos(self):
        a = self.subir("adjunto.txt", CERTIFICADO.encode("utf-8"))
        d = flujo([nodo("w", "webhook"), nodo("fin", "salida", desenlace="manual", motivo="{{archivos.cantidad}} {{entrada.cuerpo.nota}}")], [{"desde": "w", "hacia": "fin"}])
        r, _ = self.correr_orden(d, archivos=[a], entrada={"cuerpo": {"nota": "urgente"}, "archivos": [a["id"]]})
        self.assertEqual(r["motivo"], "1 urgente")

    def test_archivo_leer_con_archivo_del_flujo_y_sin_tocar_lo_de_otro(self):
        a = self.subir("lista.csv", "documento,nombre\n111,Ana\n".encode("utf-8"), aid="a-000000lista")
        d = manual(nodo("leer", "archivo_leer", archivo="a-000000lista", salida="lista"), fin=nodo("fin", "salida", desenlace="manual", motivo="{{lista.filas.0.nombre}}"))
        self.assertEqual(fd.validar(d), [])
        r, _ = self.correr_orden(d)
        self.assertEqual(r["motivo"], "Ana")
        self.assertTrue(fd._validar_config({"id": "n", "tipo": "archivo_leer", "config": {"archivo": "a-000000lista", "ruta": "x.csv"}}))
        self.subir("secreto.txt", b"privado", flujo="otro-flujo", aid="a-0000ajeno00")
        d2 = manual(nodo("leer", "archivo_leer", ruta="otro-flujo/entradas/a-0000ajeno00/secreto.txt", salida="x"))
        r, _ = self.correr_orden(d2, exigir_ok=False)
        self.assertEqual(r["accion"], "error")
        self.assertIn("espacio privado de otro flujo", r["motivo"])
        self.assertEqual(a["nombre"], "lista.csv")


class BandejaPropia(Plataforma):
    """entrada_correo lee de la bandeja PROPIA del flujo; los .eml y .msg se convierten en casos suyos."""

    EML = ("From: =?utf-8?q?Mar=C3=ADa_G=C3=B3mez?= <maria@ejemplo.test>\r\nTo: mesa@ejemplo.test\r\nSubject: =?utf-8?q?Petici=C3=B3n_de_informaci=C3=B3n?=\r\n"
           "Date: Thu, 05 Nov 2026 10:00:00 -0500\r\nMessage-ID: <abc@ejemplo.test>\r\nMIME-Version: 1.0\r\n"
           "Content-Type: multipart/mixed; boundary=\"XX\"\r\n\r\n--XX\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nSolicito copia del certificado.\r\n"
           "--XX\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Disposition: attachment; filename=\"anexo.txt\"\r\n\r\n" + CERTIFICADO + "\r\n--XX--\r\n")

    def test_eml_a_caso_y_correr(self):
        import correo_de_archivo as cda

        bandeja = fd.bandeja_del_flujo("prueba")
        r = cda.a_caso(self.EML.encode("utf-8"), "peticion.eml", bandeja)
        self.assertTrue(r["caso"].startswith("prueba-"))
        self.assertEqual(r["asunto"], "Petición de información")
        self.assertEqual(r["adjuntos"], 1)
        d = flujo([nodo("correo", "entrada_correo"), nodo("fin", "salida", desenlace="manual", motivo="{{correo.remitente_nombre}}|{{adjuntos.nombres}}")],
                  [{"desde": "correo", "hacia": "fin"}])
        res, _ = self.correr_orden(d, caso=r["caso"], flujo="prueba")
        self.assertEqual(res["motivo"], "María Gómez|anexo.txt")
        # el mismo caso no lo encuentra otro flujo en su bandeja (su carpeta es otra)
        self.assertFalse((fd.bandeja_del_flujo("otro-flujo") / r["caso"]).exists())

    def test_rechaza_lo_que_no_es_correo(self):
        import correo_de_archivo as cda

        with self.assertRaises(ValueError):
            cda.a_caso(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20, "foto.png", fd.bandeja_del_flujo("prueba"))

    def test_buzon_del_flujo_sin_credencial_explica(self):
        d = flujo([nodo("correo", "entrada_correo"), nodo("fin", "salida", desenlace="manual")], [{"desde": "correo", "hacia": "fin"}])
        r = fd.buzon_del_flujo({"flujo": "prueba", "definicion": d, "credenciales": {}})
        self.assertFalse(r["ok"])
        self.assertIn("credencial", r["error"])
        # con credencial en la config, la validación la acepta (solo el nombre)
        d["nodos"][0]["config"] = {"credencial": "buzon-soporte", "carpeta": "INBOX"}
        self.assertEqual(fd._validar_config(d["nodos"][0]), [])


# ------------------------------------------------- vueltas y ramas a la vez (llamadas a la IA en paralelo)

class ModeloLento(ModeloFalso):
    """Como el falso, pero cada llamada tarda (como la red) y se anota cuántas iban a la vez."""

    def __init__(self, segundos=0.3, **r):
        super().__init__(**r)
        self.segundos, self.en_curso, self.max_a_la_vez = segundos, 0, 0
        self._c = threading.Lock()

    def __call__(self, *a, **k):
        with self._c:
            self.en_curso += 1
            self.max_a_la_vez = max(self.max_a_la_vez, self.en_curso)
        try:
            time.sleep(self.segundos)
            return super().__call__(*a, **k)
        finally:
            with self._c:
                self.en_curso -= 1


class ALaVez(Plataforma):
    def setUp(self):
        super().setUp()
        self.modelo = ModeloLento(0.3)
        fd._llamar_modelo = self.modelo

    def bucle(self, **conf):
        cuerpo = [nodo("cada", "para_cada", lista="entrada.items", variable="x", salida="res", **conf),
                  nodo("gen", "ia_generar", instrucciones="Resume en una línea", entradas=["x"], salida="resumen"),
                  nodo("largo", "expresion", expresion="len(resumen)", salida="largo")]
        aristas = [{"desde": "inicio", "hacia": "cada"}, {"desde": "cada", "hacia": "gen", "cuando": "cada"}, {"desde": "gen", "hacia": "largo"},
                   {"desde": "cada", "hacia": "fin", "cuando": "listo"}]
        return manual(*cuerpo, aristas=aristas, campos=[{"nombre": "items", "tipo": "lista"}])

    def test_vueltas_con_ia_a_la_vez_dan_lo_mismo_que_una_tras_otra(self):
        items = [f"tarea {i}" for i in range(6)]
        d = self.bucle()
        self.assertEqual(self.problemas(d), [])
        t = time.monotonic()
        r, ev = self.correr_orden(d, entrada={"items": items})
        juntas = time.monotonic() - t
        self.assertGreater(self.modelo.max_a_la_vez, 1, "las llamadas a la IA fueron a la vez")
        self.assertLessEqual(self.modelo.max_a_la_vez, fd.A_LA_VEZ_DEFECTO)
        self.assertTrue(any(e.get("titulo", "").startswith("6 vueltas, de a 4") for e in ev if e["tipo"] == "paso"))
        # lo mismo una tras otra (concurrencia 1)
        self.modelo = ModeloLento(0.3)
        fd._llamar_modelo = self.modelo
        t = time.monotonic()
        r1, ev1 = self.correr_orden(self.bucle(concurrencia=1), entrada={"items": items})
        una = time.monotonic() - t
        self.assertEqual(self.modelo.max_a_la_vez, 1)
        self.assertEqual(r["variables"]["res"], r1["variables"]["res"], "mismo resultado, en el mismo orden")
        self.assertEqual([x["resumen"].split(chr(10))[-1] for x in r["variables"]["res"]], [f"tarea {i}" for i in range(6)])
        self.assertEqual(r["consumo"]["llamadas"], 6)
        self.assertLess(juntas, una * 0.7, f"a la vez {juntas:.2f} s, una tras otra {una:.2f} s")
        # los eventos salen en el orden de las vueltas, como una tras otra
        orden = [e["etapa"] for e in ev if e["tipo"] == "etapa_inicio" and e["etapa"] in ("gen", "largo")]
        self.assertEqual(orden, [e["etapa"] for e in ev1 if e["tipo"] == "etapa_inicio" and e["etapa"] in ("gen", "largo")])
        # el tope del nodo manda
        self.modelo = ModeloLento(0.1)
        fd._llamar_modelo = self.modelo
        self.correr_orden(self.bucle(concurrencia=2), entrada={"items": items})
        self.assertEqual(self.modelo.max_a_la_vez, 2)

    def test_lo_que_no_es_independiente_va_una_tras_otra(self):
        items = ["URGENTE revisar", "normal", "Úrgente pagar"]
        # el caso de Vera: el cuerpo escribe en una tabla → una tras otra
        d = manual(nodo("cada", "para_cada", lista="entrada.tareas", variable="t", salida="res"),
                   nodo("es", "filtro", campo="{{t.titulo}}", operador="contiene", valor="urgente"),
                   nodo("guardar", "tabla_guardar", tabla="urgentes", datos={"titulo": "{{t.titulo}}"}),
                   aristas=[{"desde": "inicio", "hacia": "cada"}, {"desde": "cada", "hacia": "es", "cuando": "cada"},
                            {"desde": "es", "hacia": "guardar", "cuando": "si"}, {"desde": "cada", "hacia": "fin", "cuando": "listo"}],
                   campos=[{"nombre": "tareas", "tipo": "lista"}])
        self.assertEqual(self.problemas(d), [])
        e = fd.Ejecucion(d, "x", "m", flujo="prueba")
        e._nodos = {n["id"]: n for n in d["nodos"]}
        self.assertEqual(e._a_la_vez(d["nodos"][1], "es", False, 3), 1, "escribe en una tabla")
        r, _ = self.correr_orden(d, entrada={"tareas": [{"titulo": x} for x in items]})
        self.assertEqual(len(fd.c_tablas.consultar("flujo-prueba", "urgentes")), 2)
        # IA sin entradas propias: lee todo el contexto (que incluye lo de otras vueltas)
        d2 = self.bucle()
        del d2["nodos"][2]["config"]["entradas"]
        e2 = fd.Ejecucion(d2, "x", "m", flujo="prueba")
        e2._nodos = {n["id"]: n for n in d2["nodos"]}
        self.assertEqual(e2._a_la_vez(d2["nodos"][1], "gen", False, 3), 1)
        # una vuelta que lee lo que dejó la anterior (un acumulado)
        d3 = self.bucle()
        d3["nodos"][3]["config"]["expresion"] = "len(resumen) + largo"
        e3 = fd.Ejecucion(d3, "x", "m", flujo="prueba")
        e3._nodos = {n["id"]: n for n in d3["nodos"]}
        self.assertEqual(e3._a_la_vez(d3["nodos"][1], "gen", False, 3), 1)
        # y la normal sí
        d4 = self.bucle()
        e4 = fd.Ejecucion(d4, "x", "m", flujo="prueba")
        e4._nodos = {n["id"]: n for n in d4["nodos"]}
        self.assertEqual(e4._a_la_vez(d4["nodos"][1], "gen", False, 3), 3)

    def test_una_vuelta_que_falla_detiene_como_una_tras_otra(self):
        self.modelo.fallos = [ValueError("se cayó")]
        r, ev = self.correr_orden(self.bucle(), exigir_ok=False, entrada={"items": ["a", "b", "c", "d"]})
        self.assertEqual(r["accion"], "error")
        d = self.bucle(al_fallar="continuar")
        self.modelo = ModeloLento(0.05)
        self.modelo.fallos = [ValueError("se cayó")]
        fd._llamar_modelo = self.modelo
        r, _ = self.correr_orden(d, entrada={"items": ["a", "b", "c", "d"]})
        res = r["variables"]["res"]
        self.assertEqual(sum(1 for x in res if "error" in x), 1)
        self.assertEqual(sum(1 for x in res if "resumen" in x), 3)

    def test_ramas_independientes_a_la_vez(self):
        nodos = [nodo("ramas", "paralelo", ramas=["a", "b", "c"]),
                 nodo("ga", "ia_generar", instrucciones="A", entradas=["entrada.texto"], salida="ra"),
                 nodo("gb", "ia_generar", instrucciones="B", entradas=["entrada.texto"], salida="rb"),
                 nodo("gc", "ia_generar", instrucciones="C", entradas=["entrada.texto"], salida="rc"),
                 nodo("junta", "unir", salida="todo")]
        aristas = [{"desde": "inicio", "hacia": "ramas"}, {"desde": "ramas", "hacia": "ga", "cuando": "a"}, {"desde": "ramas", "hacia": "gb", "cuando": "b"},
                   {"desde": "ramas", "hacia": "gc", "cuando": "c"}, {"desde": "ga", "hacia": "junta"}, {"desde": "gb", "hacia": "junta"},
                   {"desde": "gc", "hacia": "junta"}, {"desde": "junta", "hacia": "fin"}]
        d = manual(*nodos, aristas=aristas, campos=[{"nombre": "texto", "tipo": "texto"}])
        self.assertEqual(self.problemas(d), [])
        t = time.monotonic()
        r, _ = self.correr_orden(d, entrada={"texto": "hola"})
        self.assertLess(time.monotonic() - t, 0.8, "tres llamadas de 0,3 s a la vez")
        self.assertEqual(self.modelo.max_a_la_vez, 3)
        self.assertEqual(sorted(r["variables"]["todo"]), ["a", "b", "c"])
        self.assertIn("ra", r["variables"]["todo"]["a"])
        # una rama que escribe (o el nodo pide una tras otra): una tras otra
        d["nodos"][1]["config"]["concurrencia"] = 1
        self.modelo = ModeloLento(0.05)
        fd._llamar_modelo = self.modelo
        r, _ = self.correr_orden(d, entrada={"texto": "hola"})
        self.assertEqual(self.modelo.max_a_la_vez, 1)


# ------------------------------------------------- caché de conversión y OCR por huella

import cache_conversion as cc  # noqa: E402


class _ConversionFalsa:
    """Un módulo de conversión de mentira: cuenta las conversiones y deja elegir lo que devuelve."""
    __file__ = __file__
    LIMITE_PAGINAS_PDF = 60
    LIMITE_CARACTERES = 200_000
    TRANSCRIPTOR_IA = None

    def __init__(self):
        from pipeline import conversion
        self.Conversion = conversion.Conversion
        self.PresupuestoDeOcr = conversion.PresupuestoDeOcr
        self.llamadas = 0
        self.aviso = ""
        self.imagenes = 0

    def convertir(self, datos, nombre, claves_candidatas=None, presupuesto=None, **_):
        self.llamadas += 1
        if presupuesto is not None:
            presupuesto.gastar(1.5)
            presupuesto.imagenes_ia -= self.imagenes
        return self.Conversion(markdown=f"# {nombre}\n\n{datos.decode()} (lectura {self.llamadas})", tipo_real="texto", aviso=self.aviso, paginas=1)


class CacheDeConversion(Plataforma):
    def setUp(self):
        super().setUp()
        self._cache_env = {k: os.environ.get(k) for k in ("CACHE_CONVERSION", "RUTA_CACHE_CONVERSION", "CACHE_CONVERSION_MB")}
        os.environ["CACHE_CONVERSION"] = "si"
        os.environ["RUTA_CACHE_CONVERSION"] = str(self.estado / "cache")
        os.environ.pop("CACHE_CONVERSION_MB", None)
        self.conv = _ConversionFalsa()

    def tearDown(self):
        for k, v in self._cache_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        super().tearDown()

    def leer(self, datos=b"hola", nombre="a.txt", presupuesto=None, claves=None):
        p = presupuesto or self.conv.PresupuestoDeOcr()
        return cc.convertir(self.conv, self.conv.convertir, datos, nombre, claves, presupuesto=p) + (p,)

    def test_acierto_y_fallo_por_huella(self):
        c1, como1, p1 = self.leer()
        c2, como2, p2 = self.leer()
        self.assertEqual((como1, como2), ("fallo", "acierto"))
        self.assertEqual(self.conv.llamadas, 1, "la segunda no se convirtió")
        self.assertEqual(c1.markdown, c2.markdown)
        self.assertEqual(p2.gastado_s, p1.gastado_s, "el acierto descuenta lo mismo del presupuesto")
        # otro contenido u otro nombre: otra clave
        self.assertEqual(self.leer(b"chao")[1], "fallo")
        self.assertEqual(self.leer(nombre="b.txt")[1], "fallo")
        self.assertEqual(self.conv.llamadas, 3)

    def test_opciones_distintas_nunca_se_mezclan(self):
        self.leer()
        sin_ocr = self.conv.PresupuestoDeOcr()
        sin_ocr.total_s = 0.0
        self.assertEqual(self.leer(presupuesto=sin_ocr)[1], "fallo", "sin OCR es otra lectura")
        # con visión: cada proveedor y modelo es otra clave; un transcriptor sin nombre no usa la caché
        def t_a(jpeg): return "a"
        t_a.clave_cache = "vertex:gemini-2.5-flash:-"
        def t_b(jpeg): return "b"
        t_b.clave_cache = "openai:gpt-4o:abc"
        self.conv.TRANSCRIPTOR_IA = t_a
        self.assertEqual(self.leer()[1], "fallo")
        self.assertEqual(self.leer()[1], "acierto")
        self.conv.TRANSCRIPTOR_IA = t_b
        self.assertEqual(self.leer()[1], "fallo")
        self.conv.TRANSCRIPTOR_IA = lambda jpeg: "x"
        self.assertEqual(self.leer()[1], "sin_cache")
        self.conv.TRANSCRIPTOR_IA = None
        # otro tope de páginas y otras claves de PDF: otra clave (la clave del PDF no queda en disco)
        self.conv.LIMITE_PAGINAS_PDF = 5
        self.assertEqual(self.leer()[1], "fallo")
        self.assertEqual(self.leer(claves=["1023456789"])[1], "fallo")
        texto = "".join(p.read_text(encoding="utf-8") for p in (self.estado / "cache").glob("*/*.json"))
        self.assertNotIn("1023456789", texto)
        # y otra versión del motor de conversión
        cc._version[self.conv.__file__] = "otra-version"
        try:
            self.assertEqual(self.leer()[1], "fallo")
        finally:
            cc._version.pop(self.conv.__file__, None)

    def test_lo_que_quedo_a_medias_no_se_guarda(self):
        self.conv.aviso = "se agoto el presupuesto de OCR del correo"
        self.leer()
        self.assertEqual(self.leer()[1], "fallo")
        self.assertEqual(self.conv.llamadas, 2)
        # sin cupo de visión suficiente hoy, no se usa lo que costó más imágenes
        self.conv.aviso = ""
        def t(jpeg): return "x"
        t.clave_cache = "vertex:m:-"
        self.conv.TRANSCRIPTOR_IA = t
        self.conv.imagenes = 2
        self.leer(b"escaneo")
        poco = self.conv.PresupuestoDeOcr()
        poco.imagenes_ia = 1
        self.assertEqual(self.leer(b"escaneo", presupuesto=poco)[1], "fallo")
        mucho = self.conv.PresupuestoDeOcr()
        c, como, p = self.leer(b"escaneo", presupuesto=mucho)
        self.assertEqual(como, "acierto")
        self.assertEqual(p.imagenes_ia, mucho.imagenes_ia)
        self.assertEqual(p.imagenes_ia, self.conv.PresupuestoDeOcr().imagenes_ia - 2, "el acierto gasta las imágenes que costó")
        # una visión que falla (la conversión se lo calla) tampoco se guarda
        def falla(jpeg): raise RuntimeError("503")
        falla.clave_cache = "vertex:m2:-"
        self.conv.TRANSCRIPTOR_IA = falla
        orig = self.conv.convertir

        def con_vision(datos, nombre, claves=None, presupuesto=None, **k):
            try:
                self.conv.TRANSCRIPTOR_IA(b"jpeg")
            except RuntimeError:
                pass
            return orig(datos, nombre, claves, presupuesto=presupuesto)
        cc.convertir(self.conv, con_vision, b"otro", "o.txt", presupuesto=self.conv.PresupuestoDeOcr())
        self.assertEqual(cc.convertir(self.conv, con_vision, b"otro", "o.txt", presupuesto=self.conv.PresupuestoDeOcr())[1], "fallo")

    def test_tope_de_tamano_borra_lo_menos_usado(self):
        os.environ["CACHE_CONVERSION_MB"] = "0.004"  # ~4 KB
        for i in range(12):
            self.leer(f"contenido {i} ".encode() * 20, f"{i}.txt")
            time.sleep(0.01)
        self.assertLessEqual(cc.tamano(), cc.tope_bytes())
        self.assertEqual(self.leer(b"contenido 11 " * 20, "11.txt")[1], "acierto", "lo último sigue")
        self.assertEqual(self.leer(b"contenido 0 " * 20, "0.txt")[1], "fallo", "lo más viejo se borró")

    def test_leer_adjuntos_del_motor_usa_la_cache(self):
        d = cadena()
        _, ev1 = self.correr_orden(d, caso=CASO)
        _, ev2 = self.correr_orden(d, caso=CASO)
        leidos = lambda ev: {e["titulo"]: e.get("cache") for e in ev if e["tipo"] == "paso" and e.get("titulo", "").endswith(": leído")}
        self.assertTrue(leidos(ev1) and all(v == "fallo" for v in leidos(ev1).values()), leidos(ev1))
        self.assertTrue(all(v == "acierto" for v in leidos(ev2).values()), leidos(ev2))
        r1 = next(e for e in ev1 if e["tipo"] == "resultado")
        r2 = next(e for e in ev2 if e["tipo"] == "resultado")
        self.assertEqual(r1["solicitud"], r2["solicitud"])
        # con la caché apagada se lee de nuevo
        os.environ["CACHE_CONVERSION"] = "no"
        _, ev3 = self.correr_orden(d, caso=CASO)
        self.assertTrue(all(v == "sin_cache" for v in leidos(ev3).values()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
