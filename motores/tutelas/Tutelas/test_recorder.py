"""
Grabador de escrituras para MODO_TEST (dry-run) del agente de tutelas.

Cuando config.MODO_TEST=True, las escrituras (radicar/trazabilidad en SGDEA,
etiquetar/mover/responder en Outlook, guardar dedup en Redis) NO se ejecutan:
se registran aqui en CSV para validar TODO lo que el agente HARIA, sin tocar prod.

Salidas (en config.TEST_OUTPUT_DIR, por defecto ./salidas_prueba/):
  - radicaciones.csv : 1 fila por radicacion de tutela (datos que radicaria).
  - escrituras.csv   : 1 fila por cada escritura interceptada (radicar, traza,
                       etiquetar, mover, responder, redis), por correo/hilo.
"""
import csv
import json
import os
import threading
from datetime import datetime


class TestRecorder:
    _ESC = ["timestamp", "hilo", "accion", "destino", "detalle"]
    _RAD = ["timestamp", "hilo", "radicado_23", "juzgado", "codigo_juzgado",
            "doc_afectado", "tipo_doc_afectado", "fecha", "hora", "n_adjuntos"]

    def __init__(self, output_dir="salidas_prueba"):
        self.output_dir = output_dir
        self._lock = threading.Lock()
        self._ctx = ""
        os.makedirs(self.output_dir, exist_ok=True)
        self._esc_path = os.path.join(self.output_dir, "escrituras.csv")
        self._rad_path = os.path.join(self.output_dir, "radicaciones.csv")
        self._init(self._esc_path, self._ESC)
        self._init(self._rad_path, self._RAD)

    @staticmethod
    def _init(path, headers):
        if not os.path.exists(path):
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                csv.writer(f).writerow(headers)

    @staticmethod
    def _ts():
        return datetime.now().isoformat(timespec="seconds")

    def set_contexto(self, hilo=""):
        self._ctx = hilo or ""

    def registrar(self, accion, destino="", detalle=None):
        with self._lock:
            with open(self._esc_path, "a", newline="", encoding="utf-8-sig") as f:
                csv.writer(f).writerow([
                    self._ts(), self._ctx, accion, str(destino),
                    json.dumps(detalle or {}, ensure_ascii=False, default=str),
                ])

    def registrar_radicacion(self, correo, juzgado, radicado_23, n_adjuntos=0):
        datos = getattr(correo, "datos_extraidos", None)
        doc = getattr(datos, "numero_documento", "") if datos else ""
        tipo_doc = getattr(datos, "tipo_documento", "") if datos else ""
        try:
            # usar la hora local (Colombia), igual que la radicacion real
            fr = getattr(correo, "fecha_recibido_local", None) or correo.fecha_recibido
            fecha = fr.strftime("%Y-%m-%d")
            hora = fr.strftime("%H:%M")
        except Exception:
            fecha, hora = "", ""
        with self._lock:
            with open(self._rad_path, "a", newline="", encoding="utf-8-sig") as f:
                csv.writer(f).writerow([
                    self._ts(), self._ctx, radicado_23,
                    getattr(juzgado, "nombre", ""), getattr(juzgado, "codigo", ""),
                    doc, tipo_doc, fecha, hora, n_adjuntos,
                ])
        self.registrar("RADICAR_TUTELA", destino="SGDEA",
                        detalle={"radicado_23": radicado_23, "doc_afectado": doc})


_recorder = None


def get_recorder():
    global _recorder
    if _recorder is None:
        try:
            from Tutelas.config import TEST_OUTPUT_DIR
        except ImportError:
            from config import TEST_OUTPUT_DIR
        _recorder = TestRecorder(TEST_OUTPUT_DIR)
    return _recorder
