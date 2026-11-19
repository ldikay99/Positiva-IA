"""
Del manifiesto de un flujo a los comandos para desplegarlo en GCP.

    python scripts/manifiesto_a_gcp.py <flujo> --job <nombre-del-job> [--proyecto P] [--region R] [--api URL]

Lee el manifiesto y el entorno del flujo desde la plataforma y IMPRIME (no ejecuta):
- un `gcloud secrets create` por cada variable secreta, con nombre FLUJO-<ID>-<NOMBRE>,
  sin valor: el valor lo carga una persona con `gcloud secrets versions add ... --data-file=-`;
- el acceso de lectura de la cuenta de servicio del job a esos secretos;
- un `gcloud run jobs update` con las variables no secretas (--set-env-vars) y las
  secretas montadas desde Secret Manager (--set-secrets).
Nunca escribe valores secretos: la plataforma no los devuelve y este script no los pide.
Revisa los comandos antes de correrlos.
"""
import argparse
import json
import os
import shlex
import sys
import urllib.request


def pedir(api: str, ruta: str):
    with urllib.request.urlopen(api.rstrip("/") + ruta, timeout=30) as r:
        return json.loads(r.read())


def nombre_secreto(flujo: str, variable: str) -> str:
    return f"FLUJO-{flujo}-{variable}".upper().replace("_", "-")


def main() -> int:
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("flujo")
    a.add_argument("--job", required=True, help="nombre del job (o servicio) de Cloud Run")
    a.add_argument("--proyecto", default=(os.environ.get("GCP_PROJECT") or "").strip(), help="proyecto de Google Cloud (por defecto, GCP_PROJECT del entorno)")
    a.add_argument("--region", default="us-east1")
    a.add_argument("--cuenta", default="", help="cuenta de servicio del job (por defecto, un marcador en el proyecto)")
    a.add_argument("--api", default="http://localhost:8095")
    x = a.parse_args()
    if not x.proyecto:
        a.error("falta el proyecto de Google Cloud: pásalo con --proyecto o define GCP_PROJECT en el entorno")
    x.cuenta = x.cuenta or f"<cuenta-de-servicio-del-job>@{x.proyecto}.iam.gserviceaccount.com"

    manifiesto = pedir(x.api, f"/api/flujos/{x.flujo}/manifiesto")
    entorno = pedir(x.api, f"/api/flujos/{x.flujo}/entorno")
    secretas = [v for v in entorno if v.get("secreto")]
    publicas = [v for v in entorno if not v.get("secreto")]

    p = print
    p(f"# Despliegue de «{manifiesto['flujo']['nombre']}» ({x.flujo}) en {x.proyecto}")
    p(f"# Generado desde el manifiesto del {manifiesto.get('generado')}. Revísalo antes de correr nada.\n")
    if manifiesto.get("escrituras"):
        p("# Escrituras que este flujo hace afuera (habilítalas solo si el área las aprobó):")
        for e in manifiesto["escrituras"]:
            p(f"#   - {json.dumps(e, ensure_ascii=False)}")
        p("")
    if secretas:
        p("# 1. Secretos (sin valor). Carga cada valor tú: echo -n '<valor>' | gcloud secrets versions add <NOMBRE> --data-file=-")
        for v in secretas:
            s = nombre_secreto(x.flujo, v["nombre"])
            p(f"gcloud secrets create {s} --replication-policy=automatic --project={x.proyecto}")
            p(f"gcloud secrets add-iam-policy-binding {s} --member=serviceAccount:{x.cuenta} --role=roles/secretmanager.secretAccessor --project={x.proyecto}")
        p("")
    p("# 2. Variables del job")
    partes = [f"gcloud run jobs update {x.job}", f"--region={x.region}", f"--project={x.proyecto}"]
    if publicas:
        partes.append("--update-env-vars=" + shlex.quote(",".join(f"{v['nombre']}={v.get('valor', '')}" for v in publicas)))
    if secretas:
        partes.append("--update-secrets=" + ",".join(f"{v['nombre']}={nombre_secreto(x.flujo, v['nombre'])}:latest" for v in secretas))
    p(" \\\n  ".join(partes))
    sin_definir = manifiesto.get("entorno_sin_definir") or []
    if sin_definir:
        p(f"\n# OJO: el flujo usa variables que aún no están definidas: {', '.join(sin_definir)}")
    if manifiesto.get("entorno_produccion"):
        p("\n# Variables que el job de producción ya usa (de su manifiesto; revisar que estén configuradas):")
        for v in manifiesto["entorno_produccion"]:
            p(f"#   - {v['nombre']}{' (secreto)' if v.get('secreto') else ''}: {v.get('para')} [{v.get('origen')}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
