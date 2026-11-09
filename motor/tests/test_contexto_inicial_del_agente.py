"""
El agente recibe de entrada el inventario de documentos y las pistas de cada
campo pendiente, y puede buscar en un solo documento: no gasta vueltas en
ubicarse.
"""
from pipeline import agente
from pipeline.extraccion import extraer_todo
from pipeline.modelo import Llamada, ModeloFalso, Turno
from servidor_mcp.herramientas import Contexto, invocar


def _ws(tmp_path, adjuntos):
    from pipeline.materializar import materializar

    return materializar(
        id_correo="c1", asunto="Solicitud de autorizacion",
        cuerpo_limpio="Solicito autorizacion para la cita del paciente.",
        remitente_nombre="Ana", remitente_correo="ana@x.com",
        adjuntos=adjuntos, base=str(tmp_path), temporal=False)


HISTORIA = ("DATOS DEL PACIENTE\nNombres: PEDRO RUIZ\nIdentificacion: CC 41234567\n"
            "Direccion: VEREDA LA ESPERANZA FINCA EL PORVENIR\nTelefono: 3105551234\n"
            + "evolucion del paciente sin novedad\n" * 40).encode()


# ── El agente arranca con el inventario y las pistas ──

def test_la_tarea_trae_el_inventario_y_las_pistas(tmp_path):
    ws = _ws(tmp_path, [("historia.txt", HISTORIA)])
    try:
        extraccion = extraer_todo(asunto="Autorizacion", cuerpo="Solicito la cita",
                                  from_name="Ana", from_address="ana@x.com")
        assert "direccion_afectado" in extraccion.campos_a_buscar
        modelo = ModeloFalso(turnos=[Turno(texto="LISTO")])
        agente.resolver(modelo, Contexto(workspace=ws), extraccion)
        tarea = modelo.recibidos[0]["mensajes"][0]["contenido"]
        assert "DOCUMENTOS" in tarea and "historia.txt" in tarea
        assert "PISTAS" in tarea
        assert "[nombre_afectado]" in tarea and "41234567" in tarea
        # La direccion no lleva pista: el tramo que mejor responde suele ser de otro.
        assert "[direccion_afectado]" not in tarea
        # El texto del adjunto va delimitado, como en una lectura.
        assert "INICIO_DEL_DOCUMENTO" in tarea
    finally:
        ws.cerrar()


def test_sin_contexto_inicial_la_tarea_es_la_de_antes(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTE_CONTEXTO_INICIAL", "no")
    ws = _ws(tmp_path, [("historia.txt", HISTORIA)])
    try:
        extraccion = extraer_todo(asunto="Autorizacion", cuerpo="Solicito la cita",
                                  from_name="Ana", from_address="ana@x.com")
        modelo = ModeloFalso(turnos=[Turno(texto="LISTO")])
        agente.resolver(modelo, Contexto(workspace=ws), extraccion)
        tarea = modelo.recibidos[0]["mensajes"][0]["contenido"]
        assert "PISTAS" not in tarea and "DOCUMENTOS (" not in tarea
    finally:
        ws.cerrar()


def test_las_pistas_tienen_tope(tmp_path):
    ws = _ws(tmp_path, [(f"h{i}.txt", HISTORIA + str(i).encode()) for i in range(6)])
    try:
        texto = agente.pistas(ws, list(agente.PREGUNTA_DE_PISTA))
        assert len(texto) <= agente.MAX_CARACTERES_PISTAS + 200
    finally:
        ws.cerrar()


def test_reportar_en_la_primera_vuelta_cierra_sin_otra(tmp_path):
    """Con la pista, el agente reporta en la primera vuelta y no se le llama otra vez."""
    ws = _ws(tmp_path, [("historia.txt", HISTORIA)])
    try:
        extraccion = extraer_todo(asunto="Autorizacion", cuerpo="Solicito la cita",
                                  from_name="Ana", from_address="ana@x.com")
        llamadas = [Llamada("reportar_hallazgo", {
            "campo": c, "valor": "x", "evidencia": "historia.txt", "confianza": 0.9})
            for c in extraccion.campos_a_buscar]
        modelo = ModeloFalso(turnos=[Turno(llamadas=llamadas)])
        r = agente.resolver(modelo, Contexto(workspace=ws), extraccion)
        assert modelo.veces_llamado == 1 and r.iteraciones == 1
    finally:
        ws.cerrar()


# ── Buscar en un documento ──

def test_la_busqueda_se_limita_a_un_documento(tmp_path):
    ws = _ws(tmp_path, [("a.txt", b"CC 41234567 aqui"), ("b.txt", b"CC 41234567 alla")])
    try:
        ctx = Contexto(workspace=ws)
        todos = invocar(ctx, "buscar_en_documentos", {"patron": "41234567"})
        assert todos.ok and todos.datos["total"] == 2
        ids = [d["id"] for d in ws.listar() if d.get("nombre_original") == "b.txt"]
        solo = invocar(ctx, "buscar_en_documentos", {"patron": "41234567", "documento": ids[0]})
        assert solo.ok and solo.datos["total"] == 1
        por_nombre = invocar(ctx, "buscar_en_documentos",
                             {"patron": "41234567", "documento": "b.txt"})
        assert por_nombre.ok and por_nombre.datos["total"] == 1
        malo = invocar(ctx, "buscar_en_documentos", {"patron": "x", "documento": "zzz"})
        assert not malo.ok and "no existe" in malo.error
    finally:
        ws.cerrar()
