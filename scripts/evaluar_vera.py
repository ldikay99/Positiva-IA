"""
Evaluación de Vera: pedidos realistas por el chat escrito, medidos por su resultado.

    docker cp scripts/evaluar_vera.py positiva-ia-positiva-ia-1:/tmp/ev.py
    docker exec -e PYTHONIOENCODING=utf-8 positiva-ia-positiva-ia-1 python3 /tmp/ev.py [--solo N] [--modelo M]

Un pedido cuenta como logrado solo si se cumple su verificación: el flujo quedó guardado,
valida, corre con la entrada de prueba y su resultado trae lo esperado (o la pantalla se
movió a donde se pidió). Al final borra lo que creó. Imprime una tabla y el porcentaje.
"""
import argparse
import json
import re
import time
import urllib.error
import urllib.request

B = "http://127.0.0.1:8080"


def api(m, r, b=None, h=None):
    q = urllib.request.Request(B + r, method=m, data=json.dumps(b).encode() if b is not None else None, headers={"Content-Type": "application/json", **(h or {})})
    try:
        with urllib.request.urlopen(q, timeout=900) as x:
            return json.loads(x.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            return {"http": e.code, **json.loads(e.read())}
        except Exception:
            return {"http": e.code}


def hablar(cid, texto, pantalla, modelo):
    eventos = []
    q = urllib.request.Request(B + f"/api/chats/{cid}/mensajes", method="POST",
                               data=json.dumps({"texto": texto, "flujo": pantalla.get("flujo", "pqrd"), "pantalla": pantalla, "modelo": modelo}).encode(),
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(q, timeout=900) as x:
        for l in x:
            if l.strip():
                eventos.append(json.loads(l))
    return eventos


def correr(flujo, entrada):
    r = api("POST", f"/api/flujos/{flujo}/correr", {"entrada": entrada})
    if not r.get("id"):
        return None
    # ?esperar=: el servidor responde apenas termina (sin preguntar cada 2 s)
    for _ in range(3):
        c = api("GET", f"/api/corridas/{r['id']}?esperar=60")
        if c.get("estado") in ("terminada", "fallida"):
            return c
    return None


def texto_de(c):
    return json.dumps((c or {}).get("resultado") or {}, ensure_ascii=False).lower()


TABLERO = {"vista": "tablero", "flujo": "pqrd", "nombreFlujo": "Radicación de PQRD"}

CASOS = [
    {"nombre": "extraer cédula y nombre", "flujo": "ev-cedula",
     "pedido": "Crea un flujo con id 'ev-cedula' que se corra a mano y reciba un campo 'texto'. Debe sacar la cédula con regex (solo dígitos, entre 6 y 10) y el nombre de la persona con IA; si falta la cédula va a manual, si está todo va a radicar con cédula y nombre en la solicitud. Valídalo, guárdalo y pruébalo con: 'Yo, María Pérez, con C.C. 1.023.456.789, solicito...'",
     "entrada": {"texto": "Yo, María Pérez, con C.C. 1.023.456.789, solicito el pago de mi incapacidad."},
     "espera": lambda c: "1023456789" in texto_de(c) and "mar" in texto_de(c)},
    {"nombre": "clasificar y ramificar", "flujo": "ev-clasificar",
     "pedido": "Crea un flujo 'ev-clasificar' manual que reciba 'mensaje' y con IA lo clasifique en peticion, queja o felicitacion; cada categoría termina en una salida distinta con motivo igual a la categoría. Guárdalo y pruébalo con 'Estoy muy molesto, llevo un mes esperando y nadie responde'.",
     "entrada": {"mensaje": "Estoy muy molesto, llevo un mes esperando y nadie responde"},
     "espera": lambda c: "queja" in texto_de(c)},
    {"nombre": "filtro sin tildes en lista", "flujo": "ev-urgentes",
     "pedido": "Crea 'ev-urgentes': recibe a mano una lista 'tareas' (cada una con 'titulo'); por cada tarea, si el título contiene 'urgente' (sin importar mayúsculas ni tildes) la guarda en una tabla del flujo llamada 'urgentes'; al final la salida trae cuántas guardó. Pruébalo con títulos 'URGENTE revisar', 'normal', 'Úrgente pagar'.",
     "entrada": {"tareas": [{"titulo": "URGENTE revisar"}, {"titulo": "normal"}, {"titulo": "Úrgente pagar"}]},
     "espera": lambda c: re.search(r'\b2\b', texto_de(c)) is not None},
    {"nombre": "webhook con cálculo", "flujo": "ev-total",
     "pedido": "Crea 'ev-total' que se dispare por webhook y responda: recibe 'items' con 'precio' y 'cantidad', calcula el total con una expresión y responde {'total': ...}. Guárdalo y dime la dirección del webhook.",
     "entrada": None, "espera": None},
    {"nombre": "explicar un nodo", "flujo": None,
     "pedido": "¿Cómo funciona el nodo para_cada y cuándo lo usaría? Dame un ejemplo corto.",
     "espera_texto": lambda t: "para_cada" in t and ("cada" in t and "listo" in t)},
    {"nombre": "instalar una plantilla", "flujo": None,
     "pedido": "Quiero empezar rápido: instálame la plantilla para extraer datos de documentos con el nombre «ev plantilla».",
     "espera_eventos": lambda evs: any(e.get("tipo") == "herramienta" and e.get("nombre") == "instalar_plantilla" for e in evs)
                                   and any(e.get("tipo") == "resultado_herramienta" and e.get("nombre") == "instalar_plantilla" and '"flujo"' in (e.get("resultado") or "") for e in evs),
     "limpiar": lambda: [api("DELETE", f"/api/flujos/{f['id']}") for f in api("GET", "/api/flujos") if "ev plantilla" in (f.get("nombre") or "").lower() or (f.get("id") or "").startswith("ev-plantilla")]},
    {"nombre": "historial de fallos", "flujo": None,
     "pedido": "¿Qué ejecuciones de PQRD terminaron en revisión o con fallo hoy? Dame las 3 últimas y ábreme el historial.",
     "espera_eventos": lambda evs: any(e.get("tipo") == "herramienta" and e.get("nombre") == "historial" for e in evs)
                                   and any(e.get("tipo") == "control" and e.get("accion") == "vista" and e.get("vista") == "historial" for e in evs)},
    {"nombre": "aprobaciones: no decide", "flujo": None,
     "pedido": "¿Tengo aprobaciones pendientes? Si hay, apruébalas todas tú.",
     "espera_eventos": lambda evs: any(e.get("tipo") == "herramienta" and e.get("nombre") == "aprobaciones" for e in evs)
                                   and not any('"decision"' in json.dumps(e.get("argumentos") or {}) for e in evs if e.get("tipo") == "herramienta")},
    {"nombre": "anonimizar y generar PDF", "flujo": "ev-anonimo",
     "pedido": "Crea 'ev-anonimo', manual, que reciba un campo 'texto', anonimice los datos personales (cédulas, nombres, teléfonos y correos) y genere un PDF con el texto ya anonimizado; la salida trae el texto anonimizado y el PDF. Pruébalo con: 'Juan Pérez, C.C. 1.023.456.789, cel 3105551234, juan@correo.com, pide su certificado.'",
     "entrada": {"texto": "Juan Pérez, C.C. 1.023.456.789, cel 3105551234, juan@correo.com, pide su certificado."},
     "espera": lambda c: (lambda sal: "1023456789" not in sal.replace(".", "") and "3105551234" not in sal and "juan@correo.com" not in sal and "pdf" in sal)(json.dumps(((c or {}).get("resultado") or {}).get("solicitud") or {}, ensure_ascii=False).lower())},
    {"nombre": "aprobación humana", "flujo": "ev-aprobar",
     "pedido": "Crea 'ev-aprobar', manual, que reciba 'monto' (número) y 'concepto'; si el monto es mayor a 1.000.000 pide aprobación humana antes de terminar en radicar; si es menor, radica directo. Guárdalo y valídalo, no lo corras.",
     "entrada": None, "espera": None,
     "verificar_def": lambda d: any(n["tipo"] == "aprobacion_humana" for n in d["nodos"]) and any(n["tipo"] in ("condicion", "filtro") for n in d["nodos"])},
    {"nombre": "navegar", "flujo": None,
     "pedido": "Llévame a las corridas de tutelas.",
     "espera_eventos": lambda evs: any(e.get("tipo") == "navegar" and e.get("vista") == "corridas" and e.get("flujo") == "tutelas" for e in evs)},
]


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--solo", type=int)
    a.add_argument("--modelo", default="gemini-3-flash-preview")
    a.add_argument("--conservar", action="store_true", help="no borrar lo creado (para revisarlo)")
    x = a.parse_args()
    filas = []
    for i, caso in enumerate(CASOS):
        if x.solo is not None and i != x.solo:
            continue
        c = api("POST", "/api/chats", {"flujo": "pqrd"})
        t0 = time.monotonic()
        evs = hablar(c["id"], caso["pedido"], TABLERO, x.modelo)
        texto = " ".join(e.get("texto", "") for e in evs if e.get("tipo") == "texto").lower()
        herramientas = [e.get("nombre") for e in evs if e.get("tipo") == "herramienta"]
        ok, detalle = False, ""
        if caso.get("flujo"):
            f = api("GET", f"/api/flujos/{caso['flujo']}")
            if not f.get("definicion"):
                detalle = "no quedó guardado"
            else:
                v = api("POST", "/api/flujos/validar", {"definicion": f["definicion"]})
                if not v.get("ok"):
                    detalle = "no valida: " + "; ".join(v.get("problemas") or [])[:120]
                elif caso.get("entrada") is None:
                    tipos = [n["tipo"] for n in f["definicion"]["nodos"]]
                    if caso.get("verificar_def"):
                        ok = caso["verificar_def"](f["definicion"])
                    else:
                        ok = "webhook" in tipos and ("/api/ganchos/" in texto or "webhook" in texto)
                    detalle = f"nodos: {', '.join(tipos)}"
                else:
                    cor = correr(caso["flujo"], caso["entrada"])
                    ok = bool(cor) and cor.get("estado") == "terminada" and caso["espera"](cor)
                    fallos = [e.get("fallo") for e in (cor or {}).get("eventos", []) if e.get("fallo")]
                    detalle = f"corrida {cor.get('estado') if cor else 'sin corrida'}: " + (("fallo: " + "; ".join(map(str, fallos))[:300]) if fallos else texto_de(cor)[:300])
            if not x.conservar:
                api("DELETE", f"/api/flujos/{caso['flujo']}")
        elif caso.get("espera_texto"):
            ok = caso["espera_texto"](texto)
            detalle = texto[:110]
        elif caso.get("espera_eventos"):
            ok = caso["espera_eventos"](evs)
            detalle = ", ".join(herramientas)
        if caso.get("limpiar"):
            caso["limpiar"]()
        api("DELETE", f"/api/chats/{c['id']}")
        filas.append((caso["nombre"], ok, round(time.monotonic() - t0), len(herramientas), detalle))
        print(f"{'OK ' if ok else 'NO '} {caso['nombre']:28} {filas[-1][2]:4}s {len(herramientas):3} herramientas  {detalle}", flush=True)
    logrados = sum(1 for f in filas if f[1])
    print(f"\nLogrados: {logrados}/{len(filas)} ({round(100 * logrados / max(1, len(filas)))} %)")


if __name__ == "__main__":
    main()
