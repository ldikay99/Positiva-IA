"""
Resolucion del nombre del remitente (utils/generic_filter.py).

El nombre sale de una cascada de fuentes: display name del "De:", `sender` de
Graph, otros correos del hilo y firma del cuerpo. Ninguna puede entregar una
direccion de correo, un cargo ni un buzon institucional como nombre de
persona.
"""
import pytest

from utils.generic_filter import (
    es_nombre_persona_valido,
    extraer_nombre_de_firma,
    extraer_nombre_remitente_real,
    extraer_correo_remitente_real,
    is_buzon_no_humano,
    is_generic_identity,
    tiene_remitente_externo_identificable,
    _es_handle_del_correo,
    _es_solo_un_rol,
)
from utils.html_cleaner import clean_html_content


def resolver(from_name="", from_address="", cuerpo="", sender="",
             hilo=None, actual=""):
    """Envoltura legible de extraer_nombre_remitente_real."""
    return extraer_nombre_remitente_real(
        from_name, from_address, cuerpo,
        sender_name=sender, nombres_hilo=hilo, cuerpo_email_actual=actual,
    )


# ── Un cargo no identifica a nadie, venga de donde venga ──

CARGOS_COMO_DISPLAY_NAME = [
    ("Recursos Humanos", "rrhh@empresa-uno.com"),
    ("Talento Humano", "th@empresa-dos.com"),
    ("Coordinadora SST", "sst@empresa-tres.com"),
    ("Contabilidad", "conta@empresa-cuatro.com"),
    ("Area de Nomina", "pagos@empresa-cinco.com"),
]


@pytest.mark.parametrize("nombre,correo", CARGOS_COMO_DISPLAY_NAME)
def test_un_cargo_en_el_de_no_pasa_por_nombre(nombre, correo):
    """Un cargo no identifica a una persona y no puede llegar al campo nombre."""
    assert resolver(from_name=nombre, from_address=correo, cuerpo="cuerpo") == ""


@pytest.mark.parametrize("nombre,correo", CARGOS_COMO_DISPLAY_NAME)
def test_un_cargo_en_el_sender_tampoco(nombre, correo):
    assert resolver(from_address="x@empresa.com", sender=nombre,
                    cuerpo="cuerpo") == ""


@pytest.mark.parametrize("nombre,correo", CARGOS_COMO_DISPLAY_NAME)
def test_un_cargo_en_el_hilo_tampoco(nombre, correo):
    assert resolver(from_address=correo, cuerpo="cuerpo",
                    hilo=[(nombre, correo)]) == ""


def test_las_tres_fuentes_aplican_el_mismo_criterio():
    """El 'De:', el 'sender' y el hilo dan el mismo resultado ante la misma
    pareja de nombre y correo."""
    for nombre, correo in CARGOS_COMO_DISPLAY_NAME + [("Maria Gomez", "m@x.com")]:
        por_de = resolver(from_name=nombre, from_address=correo, cuerpo="c")
        por_sender = resolver(from_address="otro@x.com", sender=nombre, cuerpo="c")
        por_hilo = resolver(from_address=correo, cuerpo="c",
                            hilo=[(nombre, correo)])
        assert por_de == por_sender == por_hilo, nombre


def test_un_cargo_no_tapa_a_la_persona_de_la_firma():
    """Descartado el cargo, la cascada sigue y toma el nombre de la firma."""
    cuerpo = "Buen dia, adjunto los soportes.\nCordialmente,\nMaria Gomez Ruiz"
    assert resolver(from_name="Recursos Humanos",
                    from_address="rrhh@empresa-uno.com",
                    cuerpo=cuerpo, actual=cuerpo) == "Maria Gomez Ruiz"


def test_una_persona_con_su_cargo_al_lado_si_pasa():
    """
    Solo se descarta cuando TODO el texto es cargo. 'Coordinadora Maria Gomez'
    trae el nombre y no se puede perder.
    """
    assert resolver(from_name="Coordinadora Maria Gomez",
                    from_address="m@empresa.com", cuerpo="c") == \
        "Coordinadora Maria Gomez"


def test_una_razon_social_no_es_un_cargo():
    """Una empresa si puede ser el remitente: no se puede confundir con un area."""
    assert resolver(from_name="GRUPO 3T SAS", from_address="info@grupo3t.com",
                    cuerpo="c") == "GRUPO 3T SAS"


# ─────────────────────────────────────────────────────────────────────────
# Una direccion de correo como display name
# ─────────────────────────────────────────────────────────────────────────

# Cubren las formas que toma la parte local: nombre.apellido, buzon de area,
# area con punto y region, y un alias personal con digitos.
CORREOS_COMO_DISPLAY_NAME = [
    "jorge.pino@empresa-uno.com.co", "asistente@empresa-dos.com",
    "rhagro@empresa-tres.com", "recursoshumanos@empresa-cuatro.com",
    "contacto@empresa-cinco.com.co", "info@empresa-seis.com.co",
    "alias69@correo-personal.com", "recursohumano@empresa-siete.com.co",
    "rrhh.colombia@empresa-ocho.com", "juridica@entidad-publica.gov.co",
    "gerencia@empresa-nueve.com", "contabilidad@empresa-diez.com",
    "juridico@empresa-once.co", "administrativo@empresa-doce.com",
    "somos@empresa-trece.com",
]


@pytest.mark.parametrize("correo", CORREOS_COMO_DISPLAY_NAME)
def test_correo_nunca_es_nombre_valido(correo):
    assert es_nombre_persona_valido(correo) is False


@pytest.mark.parametrize("correo", CORREOS_COMO_DISPLAY_NAME)
def test_correo_como_display_name_no_se_devuelve(correo):
    assert resolver(from_name=correo, from_address=correo) == ""


# ─────────────────────────────────────────────────────────────────────────
# Lo que si es un nombre de persona
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("nombre", [
    "Karen Sofia Contreras Alfonso",
    "Maria Fernanda Gomez",
    "AGRUPA SALUD IPS SAS",
    "GRUPO 3T SAS",                 # razon social con digitos
    "SOLUCIONES 24/7 S.A.S",
    "Ana",                          # borde: 3 caracteres
    "O'Brien Martinez",
    "José Ramírez Peña",
])
def test_nombres_legitimos_se_aceptan(nombre):
    assert es_nombre_persona_valido(nombre) is True


@pytest.mark.parametrize("basura", [
    "", "   ", "AB", "123456", "yagopi69", "usuario123",
    "www.empresa.com", "http://x.co", "---", "@",
])
def test_basura_se_rechaza(basura):
    assert es_nombre_persona_valido(basura) is False


def test_display_name_valido_se_devuelve_intacto():
    assert resolver(
        from_name="Karen Sofia Contreras Alfonso",
        from_address="karen@empresa.com",
    ) == "Karen Sofia Contreras Alfonso"


def test_reenvio_interno_de_positiva_sigue_resolviendo():
    assert resolver(
        from_name="Notificaciones Positiva",
        from_address="notif@positiva.gov.co",
        cuerpo="De: Maria Fernanda Gomez <maria@agrempresarial.com>",
    ) == "Maria Fernanda Gomez"


# ─────────────────────────────────────────────────────────────────────────
# Buzon interno: ninguna fuente de metadatos sirve
# ─────────────────────────────────────────────────────────────────────────

def test_en_un_buzon_interno_no_se_usa_el_sender_de_graph():
    """
    Los tres campos de metadatos traen a la persona de Positiva que reenvio.
    Radicar eso pone a un funcionario como remitente de la PQRD del ciudadano.
    """
    assert resolver(
        from_name="Marcela Salgado Aguirre",
        from_address="solicitudesmedicinalaboral@positiva.gov.co",
        sender="Marcela Salgado Aguirre",
        hilo=[("Marcela Salgado Aguirre",
               "solicitudesmedicinalaboral@positiva.gov.co")],
        cuerpo="De: JORGE BARRERA NOSSA <jorge@empresa-uno.com>",
    ) == "JORGE BARRERA NOSSA"


def test_en_un_buzon_interno_sin_nada_en_el_cuerpo_no_se_inventa():
    """Vacio significa revision manual, que es mejor que radicar al funcionario."""
    assert resolver(
        from_name="Santiago Fuentes Ocampo",
        from_address="solicitudesmedicinalaboral@positiva.gov.co",
        sender="Santiago Fuentes Ocampo",
        cuerpo="Se remite la solicitud para su radicacion.",
    ) == ""


@pytest.mark.parametrize("area", [
    "Grupo de Medicina Laboral",
    "Gerencia Médica",
    "Vicepresidencia de Operaciones",
    "Área de Seguridad y Salud en el Trabajo",
    "Grupo de Atención Integral y de Servicio al Ciudadano",
])
def test_un_area_no_es_el_nombre_de_quien_escribe(area):
    """
    Son las lineas que firman los correos institucionales. Tomarlas radica un
    area en el campo del remitente.
    """
    assert resolver(
        from_name="Notificaciones", from_address="notif@positiva.gov.co",
        actual=f"Cordialmente,\n{area}\nPositiva Compañia de Seguros",
    ) == ""


def test_un_cargo_con_nexos_dentro_de_un_marco_de_area_se_descarta():
    """
    `_es_solo_un_rol` quita los nexos antes de comparar, asi que una frase del
    vocabulario que LLEVA nexos no podia coincidir dentro de "Area de ...".
    """
    from utils.generic_filter import _es_solo_un_rol

    assert _es_solo_un_rol("Area de Seguridad y Salud en el Trabajo")
    assert _es_solo_un_rol("Oficina de Servicio al Cliente")
    assert not _es_solo_un_rol("Grupo Empresarial Andino SAS")


@pytest.mark.parametrize("fn,fa,esperado", [
    ("X", "a@positiva.gov.co", True),
    ("Karen", "k@empresa.com", False),
])
def test_is_buzon_no_humano_sin_cambios(fn, fa, esperado):
    assert is_buzon_no_humano(fn, fa) is esperado


def test_funciones_vecinas_sin_cambios():
    assert extraer_correo_remitente_real("k@empresa.com", "", "Karen") == "k@empresa.com"
    assert is_generic_identity("INDEPENDIENTE VOLUNTARIO", "899999998") is True
    assert tiene_remitente_externo_identificable("Karen", "k@empresa.com", "") is True


# ─────────────────────────────────────────────────────────────────────────
# Fuentes de recuperacion, para no mandar a revision manual de mas
# ─────────────────────────────────────────────────────────────────────────

def test_recupera_del_campo_sender_de_graph():
    """'sender' difiere de 'De:' en envios delegados."""
    assert resolver(
        from_name="contacto@mywak.com.co", from_address="contacto@mywak.com.co",
        sender="Maria Fernanda Gomez",
    ) == "Maria Fernanda Gomez"


@pytest.mark.parametrize("sender", [
    "otro@mywak.com.co",       # es un correo
    "contacto",                # es el handle del buzon
    "Recursos Humanos",        # es un area
    "Notificaciones Positiva",  # es interno
])
def test_sender_no_usable_se_ignora(sender):
    assert resolver(
        from_name="contacto@mywak.com.co", from_address="contacto@mywak.com.co",
        sender=sender,
    ) == ""


def test_recupera_del_hilo_solo_del_mismo_remitente():
    assert resolver(
        from_name="contacto@mywak.com.co", from_address="contacto@mywak.com.co",
        hilo=[("contacto@mywak.com.co", "contacto@mywak.com.co"),
              ("Sandra Milena Ruiz", "contacto@mywak.com.co")],
    ) == "Sandra Milena Ruiz"


def test_no_toma_el_nombre_de_otro_remitente_del_hilo():
    assert resolver(
        from_name="contacto@mywak.com.co", from_address="contacto@mywak.com.co",
        hilo=[("Pedro Otra Empresa", "pedro@otraempresa.com")],
    ) == ""


@pytest.mark.parametrize("cuerpo,esperado", [
    ("De: Maria Fernanda Gomez <maria@agrempresarial.com>", "Maria Fernanda Gomez"),
    ("De: Sandra Ruiz [mailto:sandra@agrempresarial.com]", "Sandra Ruiz"),
    ("De: Carlos Perez (carlos@agrempresarial.com)", "Carlos Perez"),
    ("From: Diana Lopez <diana@agrempresarial.com>", "Diana Lopez"),
    ("> De: Elena Marin <elena@agrempresarial.com>", "Elena Marin"),
    ("De: Andrea Salazar Gomez\nEnviado: lunes", "Andrea Salazar Gomez"),
])
def test_formatos_de_encabezado_de_reenvio(cuerpo, esperado):
    assert resolver(
        from_name="contacto@mywak.com.co", from_address="contacto@mywak.com.co",
        cuerpo=cuerpo,
    ) == esperado


@pytest.mark.parametrize("cuerpo", [
    "De: Analista Uno <analista@positiva.gov.co>",       # interno
    "De: Alertas Sistema <noreply@banco.com>",           # no humano
    "De: contacto@mywak.com.co <contacto@mywak.com.co>",  # el nombre es el correo
    "De: Recursos Humanos\nEnviado: lunes",              # es un area
])
def test_encabezados_que_no_se_deben_tomar(cuerpo):
    assert resolver(
        from_name="contacto@mywak.com.co", from_address="contacto@mywak.com.co",
        cuerpo=cuerpo,
    ) == ""


# ─────────────────────────────────────────────────────────────────────────
# Firma del correo
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("cuerpo,esperado", [
    ("Buen dia.\nCordialmente, Maria Fernanda Gomez", "Maria Fernanda Gomez"),
    ("Favor radicar.\nAtentamente,\nLuz Marina Moreno\nMadre del afiliado",
     "Luz Marina Moreno"),
    ("Adjuntamos soportes.\nCordialmente,\nAGRUPA SALUD IPS SAS",
     "AGRUPA SALUD IPS SAS"),
    ("ok\nSaludos\nCarlos Andres Ruiz", "Carlos Andres Ruiz"),
    ("Gracias\nAtentamente\nGloria Ines Cardona", "Gloria Ines Cardona"),
    # Recorta el cargo y los datos de contacto pegados al nombre
    ("ok\nCordialmente,\nDiana Lopez | Coordinadora SST | 3105551234",
     "Diana Lopez"),
    ("ok\nCordialmente,\nMartha Ligia Osorio CC 41234567", "Martha Ligia Osorio"),
    ("Adjunto soportes.\nCordialmente, Luz Marina Moreno - Jefe de Personal",
     "Luz Marina Moreno"),
])
def test_firma_recupera_el_nombre(cuerpo, esperado):
    assert extraer_nombre_de_firma(cuerpo) == esperado


@pytest.mark.parametrize("cuerpo", [
    "Favor radicar.\nCordialmente,\nRecursos Humanos",    # area
    "ok\nAtentamente,\nAnalista de Nomina",               # cargo
    "ok\nCordialmente,\nTel: 3001234567",                 # telefono
    "ok\nCordialmente,\ncontacto@mywak.com.co",           # correo
    "ok\nCordialmente,\nPositiva Compania de Seguros",    # Positiva
    "ok\nCordialmente,\nGracias",                         # un token suelto
    "ok\nCordialmente,\n-\n-\n-\nMaria Gomez",            # demasiado lejos
    "Favor radicar la incapacidad del trabajador Juan Perez",  # sin despedida
])
def test_firma_no_toma_lo_que_no_es_nombre(cuerpo):
    assert extraer_nombre_de_firma(cuerpo) == ""


def test_la_firma_del_hilo_no_se_usa_solo_la_del_correo_actual():
    """La firma de un mensaje anterior del hilo pertenece a otra persona."""
    assert resolver(
        from_name="contacto@mywak.com.co", from_address="contacto@mywak.com.co",
        cuerpo="Cordialmente, Persona De Correo Anterior", actual="",
    ) == ""
    assert resolver(
        from_name="contacto@mywak.com.co", from_address="contacto@mywak.com.co",
        cuerpo="cualquier cosa", actual="Cordialmente, Persona Correcta",
    ) == "Persona Correcta"


# ─────────────────────────────────────────────────────────────────────────
# Frases, recortes y razones sociales que no son un nombre
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("frase", [
    "Se adjuntan los siguientes documentos",
    "Favor radicar el caso adjunto",
    "Quedamos a la espera de su respuesta",
    "Adjunto encontrara los soportes",
    "Se remite para su tramite",
    "Agradezco confirmar el recibido",
])
def test_una_frase_del_cuerpo_no_es_un_nombre(frase):
    """La capitalizacion distingue un nombre de una frase del cuerpo: un nombre la lleva, una frase no."""
    assert extraer_nombre_de_firma(f"ok\nGracias\n{frase}") == ""


@pytest.mark.parametrize("linea", [
    "Copia de la CC 12345678",
    "Copia de la cedula 41234567",
    "Adjunto la incapacidad 900123",
    "Anexo el documento CC 123456",
    "Copia Cedula Trabajador",
    "Documentos del Afiliado",
    "Incapacidad del Trabajador",
])
def test_el_recorte_no_deja_fragmentos_que_parezcan_nombre(linea):
    """Un nombre nunca empieza ni termina en preposicion o articulo."""
    assert extraer_nombre_de_firma(f"ok\nGracias\n{linea}") == ""


@pytest.mark.parametrize("nombre", [
    "Maria de los Angeles Rojas",
    "Juan de la Cruz Martinez",
    "AGRUPA DE LA COSTA SAS",
    "Luz del Carmen Pineda",
])
def test_preposiciones_internas_no_rompen_el_nombre(nombre):
    assert extraer_nombre_de_firma(f"ok\nGracias\n{nombre}") == nombre


@pytest.mark.parametrize("empresa", [
    "GRUPO 3T SAS",
    "ASESORIA LEGAL SAS",
    "AREA ANDINA S.A.S",
    "Oficina Juridica Martinez Abogados",
    "CENTRO MEDICO LOS ANDES",
    "UNIDAD MEDICA DEL NORTE",
    "DIRECCION GENERAL DE OBRAS SAS",
])
def test_razon_social_no_se_confunde_con_un_cargo(empresa):
    """Un filtro de cargos por prefijo no puede rechazar empresas legitimas."""
    assert _es_solo_un_rol(empresa) is False


@pytest.mark.parametrize("rol", [
    "Recursos Humanos", "RRHH", "Nomina", "Analista de Nomina",
    "Area de Cartera", "Departamento Juridico", "Coordinacion de SST",
    "Jefe de Personal", "Servicio al Cliente", "Notificaciones",
    "Auxiliar Administrativa", "Revisor Fiscal", "Representante Legal",
])
def test_cargos_reales_siguen_descartados(rol):
    assert _es_solo_un_rol(rol) is True


def test_handle_del_propio_buzon():
    assert _es_handle_del_correo(
        "recursoshumanos", "recursoshumanos@agrempresarial.com") is True
    assert _es_handle_del_correo(
        "Recursos Humanos", "recursoshumanos@agrempresarial.com") is True
    assert _es_handle_del_correo("Karen Contreras", "karen@x.com") is False


# ─────────────────────────────────────────────────────────────────────────
# Orden de la cascada
# ─────────────────────────────────────────────────────────────────────────

def test_el_display_name_valido_gana_sobre_las_demas_fuentes():
    assert resolver(
        from_name="Karen Sofia Contreras", from_address="karen@empresa.com",
        sender="Otro Nombre", hilo=[("Tercero", "karen@empresa.com")],
        cuerpo="De: Cuarto Nombre <c@x.com>", actual="Cordialmente, Quinto Nombre",
    ) == "Karen Sofia Contreras"


def test_la_firma_del_mensaje_actual_gana_sobre_el_encabezado_del_hilo():
    """
    Quien firma el mensaje que llego es quien lo escribio. Un 'De:' del cuerpo
    pertenece a un mensaje anterior del hilo, y ese es de otra persona.
    """
    assert resolver(
        from_name="contacto@mywak.com.co", from_address="contacto@mywak.com.co",
        cuerpo="De: Maria Gomez <maria@agrempresarial.com>",
        actual="Cordialmente, Otro Distinto",
    ) == "Otro Distinto"


def test_el_encabezado_gana_si_la_firma_es_de_un_buzon_interno():
    """
    Es la comprobacion que sustituye a la del dominio: la firma no trae correo
    al lado, asi que se valida contra el 'De:' que el hilo trae mas arriba.
    """
    cuerpo = ("De: Maria Gomez <maria@agrempresarial.com>\n"
              "De: Otro Distinto <otro.distinto@positiva.gov.co>\n")
    assert resolver(
        from_name="contacto@mywak.com.co", from_address="contacto@mywak.com.co",
        cuerpo=cuerpo, actual=f"{cuerpo}Cordialmente, Otro Distinto",
    ) == "Maria Gomez"


# ─────────────────────────────────────────────────────────────────────────
# Cuando dos nombres son de la misma persona
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("uno,otro", [
    ("FABIOLA ROBLEDO REVELO", "FABIOLA ROBLEDO"),
    ("Maria del Carmen Gomez Perez", "Maria Gomez Perez"),
    ("PEDRO RUIZ MEJIA", "Pedro Ruíz Mejía"),
    ("Johani Valencia Lopez", "JOHANI VALENCIA LOPEZ"),
])
def test_la_forma_corta_es_la_misma_persona(uno, otro):
    from utils.generic_filter import es_la_misma_persona

    assert es_la_misma_persona(uno, otro)
    assert es_la_misma_persona(otro, uno)


@pytest.mark.parametrize("uno,otro", [
    ("PEDRO RUIZ MEJIA", "ANA RUIZ LOPEZ"),
    ("Carlos Rangel Garces", "Fabiola Hurtado Salgado"),
    ("JUAN PEREZ", ""),
])
def test_dos_personas_distintas_no_se_confunden(uno, otro):
    """Compartir un apellido no las convierte en la misma persona."""
    from utils.generic_filter import es_la_misma_persona

    assert not es_la_misma_persona(uno, otro)


def test_las_particulas_no_deciden_la_identidad():
    """'de la' esta en media Colombia y no identifica a nadie."""
    from utils.generic_filter import es_la_misma_persona

    assert not es_la_misma_persona("Maria de la Rosa", "Pedro de la Cruz")


def test_la_firma_completa_gana_sobre_un_display_name_de_una_palabra():
    """
    El display name trae a veces solo el nombre de pila; la radicacion necesita
    el nombre completo, y la firma del correo actual lo tiene.
    """
    assert resolver(
        from_name="DOLORES", from_address="asvega2007@empresa-dos.com.co",
        actual="Atentamente\nALEJANDRA SANDOVAL VEGA\nC.C. 41234567",
    ) == "ALEJANDRA SANDOVAL VEGA"


def test_la_firma_completa_gana_sobre_un_sender_de_una_palabra():
    """Igual cuando el nombre de una palabra viene del 'sender' de Graph."""
    assert resolver(
        from_name="talentohumano@servicios.com",
        from_address="talentohumano@servicios.com",
        sender="Rosalba",
        actual="Cordialmente,\nCARID HERNANDEZ PALACIOS\nLider de Talento Humano",
    ) == "CARID HERNANDEZ PALACIOS"


def test_un_display_name_de_una_palabra_se_conserva_si_no_hay_firma():
    """Sin firma utilizable, una palabra es mejor que nada."""
    assert resolver(
        from_name="DOLORES", from_address="asvega2007@empresa-dos.com.co",
        actual="Buenos dias, adjunto los documentos solicitados.",
    ) == "DOLORES"


def test_la_firma_de_un_mensaje_anterior_no_reemplaza_al_display_name():
    """
    La preferencia mira solo el cuerpo del correo ACTUAL: la firma del hilo es
    de otra persona y tomarla radicaria a quien no escribio.
    """
    assert resolver(
        from_name="DOLORES", from_address="asvega2007@empresa-dos.com.co",
        cuerpo="Atentamente\nJORGE IVAN SANDOVAL ALZATE",
        actual="Reenvio el mensaje anterior.",
    ) == "DOLORES"


# ─────────────────────────────────────────────────────────────────────────
# HTML de correo real, pasando por el limpiador de produccion
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("html,esperado", [
    # Firma en tabla, una celda por fila (Outlook clasico)
    ("<div>Buen dia.</div><div>Cordialmente,</div><table>"
     "<tr><td><b>Maria Fernanda Gomez</b></td></tr>"
     "<tr><td>Analista de Nomina</td></tr></table>", "Maria Fernanda Gomez"),
    # Logo y nombre en la misma fila
    ("<div>ok</div><div>Cordialmente,</div><table><tr>"
     "<td><img src='logo.png'></td><td>Sandra Milena Ruiz</td></tr></table>",
     "Sandra Milena Ruiz"),
    # Entidades HTML y mayusculas con acentos
    ("<p>Buen dia</p><p>Cordialmente,</p>"
     "<p>JOS&Eacute; RAM&Iacute;REZ PE&Ntilde;A</p>", "JOSE RAMIREZ PENA"),
    # Saltos de linea Windows y espacios duros
    ("<p>Buenas</p>\r\n<p>Cordialmente,</p>\r\n<p>&#160;Ruben Dario Salas&#160;</p>",
     "Ruben Dario Salas"),
    # Salta el area y toma la razon social
    ("<div>ok</div><div>Cordialmente,</div><div>Recursos Humanos</div>"
     "<div>AGREMPRESARIAL SAS</div>", "AGREMPRESARIAL SAS"),
    # Reenvio anidado: gana el mas reciente
    ("<div>Reenvio.</div><div>De: Ana Torres &lt;ana@clienteuno.com&gt;</div>"
     "<div>De: Beto Gil &lt;beto@clientedos.com&gt;</div>", "Ana Torres"),
])
def test_html_real_resuelve_el_nombre(html, esperado):
    limpio = clean_html_content(html)
    assert resolver(
        from_name="recursoshumanos@agrempresarial.com",
        from_address="recursoshumanos@agrempresarial.com",
        cuerpo=limpio, actual=limpio,
    ) == esperado


@pytest.mark.parametrize("html", [
    "<div>Favor revisar adjunto</div><div>Enviado desde mi iPhone</div>",
    "<div>Adjunto.</div><div>Cordialmente,</div>"
    "<div>Departamento Juridico</div><div>Tel 6013334444</div>",
    "",
])
def test_html_real_sin_nombre_no_inventa(html):
    """Sin nombre confiable devuelve vacio: el orquestador lo recupera con IA
    o manda el correo a revision manual."""
    limpio = clean_html_content(html)
    assert resolver(
        from_name="recursoshumanos@agrempresarial.com",
        from_address="recursoshumanos@agrempresarial.com",
        cuerpo=limpio, actual=limpio,
    ) == ""


# ─────────────────────────────────────────────────────────────────────────
# Robustez
# ─────────────────────────────────────────────────────────────────────────

def test_entradas_nulas_no_revientan():
    assert resolver(from_name=None, from_address=None, cuerpo=None) == ""
    assert resolver(from_name="a@b.co", from_address="a@b.co", hilo=[(None, None)]) == ""
    assert extraer_nombre_de_firma(None) == ""


# ─────────────────────────────────────────────────────────────────────────
# El encabezado de reenvio trae una empresa; el payload necesita a la persona
# ─────────────────────────────────────────────────────────────────────────

BUZON = "notificaciones@positiva.gov.co"


def test_la_razon_social_del_encabezado_cede_a_quien_firma():
    """
    El encabezado se prefiere porque trae el correo y se puede validar contra el
    dominio, pero una razon social no nombra a la persona que escribe.
    """
    cuerpo = (
        "De: MINIPRECIOS&SURTIBABY <contacto@empresa-uno.com>\n"
        "Solicitamos el estado de cartera.\n"
        "Cordialmente,\nMaria Perez\nArea de Talento Humano\n"
    )
    assert resolver(from_name="Notificaciones", from_address=BUZON,
                    cuerpo=cuerpo, actual=cuerpo) == "Maria Perez"


def test_sin_persona_que_firme_la_razon_social_se_conserva():
    """No se pierde cobertura: el respaldo del encabezado sigue disponible."""
    cuerpo = ("De: MINIPRECIOS&SURTIBABY <contacto@empresa-uno.com>\n"
              "Solicitamos el estado de cartera.\n")
    assert resolver(from_name="Notificaciones", from_address=BUZON,
                    cuerpo=cuerpo, actual=cuerpo) == "MINIPRECIOS&SURTIBABY"


def test_la_firma_de_un_funcionario_de_positiva_no_gana():
    """
    Una firma no trae correo al lado, pero el hilo empareja ese mismo nombre con
    una direccion interna: quien firma reenvio el correo, no lo origino.
    """
    cuerpo = (
        "De: MINIPRECIOS&SURTIBABY <contacto@empresa-uno.com>\n"
        "De: Maria Inocencia Cantillo Polo - Contratista "
        "<maria.cantillo@positiva.gov.co>\n"
        "Buena tarde\n"
        "Cordialmente,\nMaria Inocencia Cantillo Polo\n"
    )
    assert resolver(from_name="Notificaciones", from_address=BUZON,
                    cuerpo=cuerpo, actual=cuerpo) == "MINIPRECIOS&SURTIBABY"


def test_la_firma_de_un_externo_si_gana_al_encabezado_de_empresa():
    cuerpo = (
        "De: CONSTRUCTORA GED S.A.S <sst@empresa-dos.com>\n"
        "Remitimos el derecho de peticion.\n"
        "Cordialmente,\nClaudia Vega Parra\nResidente SST\n"
    )
    assert resolver(from_name="Notificaciones", from_address=BUZON,
                    cuerpo=cuerpo, actual=cuerpo) == "Claudia Vega Parra"


def test_tras_descartar_la_firma_interna_se_sigue_con_las_demas():
    """
    Un reenvio apila varias firmas. Descartar la primera por ser de un buzon
    interno no puede abandonar el cuerpo: la del ciudadano viene despues.
    """
    cuerpo = (
        "De: Maria Inocencia Cantillo Polo - Contratista "
        "<maria.cantillo@positiva.gov.co>\n"
        "Solicito los datos del afiliado.\n"
        "Cordialmente,\nMaria Inocencia Cantillo Polo\n"
        "De: Auxiliar Siniestro GIS SAS <auxiliar@empresa-tres.co>\n"
        "Adjunto lo solicitado.\n"
        "Cordialmente,\nAnyulli Viviana Zapata Marulanda\n"
    )
    assert resolver(from_name="Notificaciones", from_address=BUZON,
                    cuerpo=cuerpo,
                    actual=cuerpo) == "Anyulli Viviana Zapata Marulanda"


# ── Un buzon no es una persona ──

@pytest.mark.parametrize("nombre", [
    "Notificaciones Judiciales",
    "NotificacionesPHP",
    "Notificaciones en General",
    "Notificacion_Servicios",
    "NOTIFICACION ACUEDUCTO DE BOGOTA",
    "Notificaciones Oriente",
    "Notificaciones SGD Orfeo",
    "notificaciones notificaciones",
    "SOPORTE TECNICO",
    "Recepcion",
    "INFORMACION DEL CONTRATANTE",
])
def test_el_nombre_de_un_buzon_no_identifica_a_nadie(nombre):
    """
    Se llaman por su funcion mas una palabra cualquiera —"Judiciales",
    "Oriente", "SGD Orfeo"— asi que exigir que TODAS las palabras sean del
    vocabulario de cargos los deja pasar. Lo que los delata es con que
    empiezan.
    """
    from utils.generic_filter import motivo_por_el_que_no_identifica_a_nadie

    assert motivo_por_el_que_no_identifica_a_nadie(nombre)


def test_una_direccion_de_correo_no_es_un_nombre():
    from utils.generic_filter import motivo_por_el_que_no_identifica_a_nadie

    assert motivo_por_el_que_no_identifica_a_nadie(
        "notificacionjudicial@gilyepesycia.com")


@pytest.mark.parametrize("nombre", [
    "Andres Felipe Rincon",
    "GRUPO 3T SAS",
    "Agricola Oro Persea Zomac S.A.S.",
    # Empiezan como un buzon y no lo son: su marca de sociedad lo dice.
    "Asistencia Medica Domiciliaria SAS",
    "Notificaciones Alvarez S.A.S.",
    # Empieza por las mismas letras que "info" y es una empresa.
    "Informatica Colombiana",
])
def test_una_persona_o_una_empresa_si_identifican(nombre):
    from utils.generic_filter import motivo_por_el_que_no_identifica_a_nadie

    assert not motivo_por_el_que_no_identifica_a_nadie(nombre), nombre
