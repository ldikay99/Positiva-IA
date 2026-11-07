"""
Copia las variables y los secretos de un job de Cloud Run al entorno cifrado de un flujo.

    python scripts/copiar_entorno_de_job.py <job> <flujo> [--proyecto P] [--region R] [--api URL] [--simular]

- Lee la configuración del job con `gcloud run jobs describe` (la cuenta de gcloud de
  quien lo corre) y, para cada variable que viene de Secret Manager, su valor con
  `gcloud secrets versions access latest`.
- Guarda cada una en `PUT /api/flujos/<flujo>/entorno/<nombre>` de la plataforma, como
  SECRETO si viene de Secret Manager o su nombre parece de clave (PASSWORD, SECRET,
  KEY, TOKEN); la plataforma la cifra (AES-256-GCM) y nunca la vuelve a mostrar.
- Manda la confirmación de persona (`X-Confirmado-Por-Persona: si`): correr este script
  ES esa confirmación, y solo debe correrlo quien está autorizado a copiar esos secretos.
- NUNCA imprime, registra ni escribe a disco un valor: solo nombres y si quedó guardado.
- Con --simular solo lista lo que copiaría.
"""
import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request

PARECE_SECRETO = re.compile(r"PASSWORD|SECRET|KEY|TOKEN|CLAVE", re.I)


def gcloud(*args: str) -> str:
    r = subprocess.run(["gcloud", *args], capture_output=True, text=True, encoding="utf-8", shell=sys.platform == "win32")
    if r.returncode != 0:
        # El error de gcloud no trae valores; igual se recorta.
        raise RuntimeError((r.stderr or "gcloud falló").strip().splitlines()[-1][:200])
    return r.stdout


def poner(api: str, flujo: str, nombre: str, valor: str, secreto: bool, descripcion: str) -> str:
    cuerpo = json.dumps({"valor": valor, "secreto": secreto, "descripcion": descripcion}).encode()
    q = urllib.request.Request(f"{api.rstrip('/')}/api/flujos/{flujo}/entorno/{urllib.request.quote(nombre, safe='')}", data=cuerpo, method="PUT",
                               headers={"Content-Type": "application/json", "X-Confirmado-Por-Persona": "si"})
    try:
        with urllib.request.urlopen(q, timeout=30) as r:
            r.read()
            return "guardada"
    except urllib.error.HTTPError as e:
        try:
            return "rechazada: " + str(json.loads(e.read()).get("error"))[:160]
        except Exception:
            return f"rechazada: HTTP {e.code}"


def main() -> int:
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("job")
    a.add_argument("flujo")
    a.add_argument("--proyecto", default="positiva-sgda-prd-416417")
    a.add_argument("--region", default="us-east1")
    a.add_argument("--api", default="http://localhost:8095")
    a.add_argument("--simular", action="store_true")
    x = a.parse_args()

    d = json.loads(gcloud("run", "jobs", "describe", x.job, "--project", x.proyecto, "--region", x.region, "--format", "json"))
    contenedor = d["spec"]["template"]["spec"]["template"]["spec"]["containers"][0]
    resultados = []
    for e in contenedor.get("env", []):
        nombre = e["name"]
        ref = (e.get("valueFrom") or {}).get("secretKeyRef")
        try:
            if ref:
                valor = gcloud("secrets", "versions", "access", ref.get("key") or "latest", "--secret", ref["name"], "--project", x.proyecto)
                origen = f"Secret Manager: {ref['name']}"
            else:
                valor = e.get("value", "")
                origen = "valor directo en el job"
            secreto = bool(ref) or bool(PARECE_SECRETO.search(nombre))
            estado = "simulada" if x.simular else poner(x.api, x.flujo, nombre, valor, secreto, f"Copiada del job {x.job} ({origen})")
            resultados.append((nombre, "secreto" if secreto else "público", estado))
        except Exception as err:
            resultados.append((nombre, "?", f"error: {err}"))
        finally:
            valor = None  # noqa: F841 — que no quede referencia al valor
    for nombre, tipo, estado in resultados:
        print(f"{nombre:42} {tipo:8} {estado}")
    return 0 if all(r[2] in ("guardada", "simulada") for r in resultados) else 1


if __name__ == "__main__":
    sys.exit(main())
