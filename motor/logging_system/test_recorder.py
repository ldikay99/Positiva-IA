"""
Grabador de escrituras para MODO_TEST.

Cuando settings.MODO_TEST=True, las escrituras del agente (radicar/traza/asignar
en SGDEA, etiquetar/mover/reenviar en Outlook, guardar dedup en Redis y en la base de radicados)
NO se ejecutan: se registran aqui en archivos CSV para poder validar TODO lo que
el agente HARIA, sin tocar produccion.

Salidas (en settings.TEST_OUTPUT_DIR, por defecto ./salidas_prueba/):
  - radicaciones.csv : 1 fila por radicacion -> tabla para validar los datos extraidos.
  - escrituras.csv   : 1 fila por cada escritura interceptada (radicar, traza,
                       asignar, etiquetar, mover, reenviar, redis, registro),
                       correlacionada con el correo por el contexto.
"""
import csv
import json
import os
import threading
from datetime import datetime


class TestRecorder:
    """Singleton simple, thread-safe, que apila escrituras en CSV."""

    _ESC_HEADERS = [
        "timestamp", "email_id", "asunto", "accion", "destino", "detalle",
    ]
    _RAD_HEADERS = [
        "timestamp", "email_id", "asunto",
        "ced_remitente", "nombre_remitente",
        "ced_afectado", "nombre_afectado", "correo_afectado", "tel_afectado",
        "genero_afectado", "tipo_pqrd", "tipo_solicitud", "oficina", "n_adjuntos",
    ]

    def __init__(self, output_dir: str = "salidas_prueba"):
        self.output_dir = output_dir
        self._lock = threading.Lock()
        self._ctx = {"email_id": "", "asunto": "", "remitente": ""}
        os.makedirs(self.output_dir, exist_ok=True)
        self._esc_path = os.path.join(self.output_dir, "escrituras.csv")
        self._rad_path = os.path.join(self.output_dir, "radicaciones.csv")
        self._init_csv(self._esc_path, self._ESC_HEADERS)
        self._init_csv(self._rad_path, self._RAD_HEADERS)

    @staticmethod
    def _init_csv(path: str, headers: list) -> None:
        if not os.path.exists(path):
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                csv.writer(f).writerow(headers)

    @staticmethod
    def _ts() -> str:
        return datetime.now().isoformat(timespec="seconds")

    def set_contexto(self, email_id: str = "", asunto: str = "", remitente: str = "") -> None:
        """Fija el correo actual para correlacionar las escrituras siguientes."""
        self._ctx = {"email_id": email_id, "asunto": asunto or "", "remitente": remitente or ""}

    def registrar(self, accion: str, destino: str = "", detalle=None) -> None:
        """Registra una escritura generica interceptada."""
        with self._lock:
            with open(self._esc_path, "a", newline="", encoding="utf-8-sig") as f:
                csv.writer(f).writerow([
                    self._ts(),
                    self._ctx["email_id"],
                    self._ctx["asunto"],
                    accion,
                    str(destino),
                    json.dumps(detalle or {}, ensure_ascii=False, default=str),
                ])

    def registrar_radicacion(self, payload, n_adjuntos: int = 0) -> None:
        """Registra el payload de radicacion en la tabla de validacion + escrituras."""
        with self._lock:
            with open(self._rad_path, "a", newline="", encoding="utf-8-sig") as f:
                csv.writer(f).writerow([
                    self._ts(),
                    self._ctx["email_id"],
                    self._ctx["asunto"],
                    payload.datos_remitente_numero_documento,
                    payload.datos_remitente_nombre,
                    payload.datos_afectado_numero_documento,
                    payload.datos_afectado_nombre,
                    payload.datos_afectado_correo_electronico,
                    payload.datos_afectado_telefono_celular,
                    payload.datos_afectado_genero_id,
                    payload.solicitud_tipo_pqrd_id,
                    payload.solicitud_tipo_solicitud_id,
                    payload.oficina,
                    n_adjuntos,
                ])
        # tambien queda en el log unificado de escrituras
        self.registrar("RADICAR", destino="SGDEA", detalle=payload.to_form_data())


# Singleton perezoso: solo se crea cuando MODO_TEST hace el primer import.
_recorder = None


def get_recorder():
    global _recorder
    if _recorder is None:
        from config import settings
        _recorder = TestRecorder(getattr(settings, "TEST_OUTPUT_DIR", "salidas_prueba"))
    return _recorder
