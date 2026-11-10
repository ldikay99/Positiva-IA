"""
La marca de un documento: logo en el encabezado, banner (franja arriba y/o abajo),
marca de agua (imagen o texto, con opacidad, tamaño y rotación, centrada en cada
página o diapositiva) y colores y fuente, para Word, PDF, PowerPoint, HTML e
imágenes. La usan el chat (generar_documento y aplicar_marca de Vera) y los flujos
(config «marca» de documento_generar y documento_plantilla, y el nodo
documento_marca, que la estampa en muchos documentos a la vez).

    marca = {
      "logo":       {"imagen": ref, "posicion": "izquierda|centro|derecha", "ancho_cm": 3.5},
      "banner":     {"imagen": ref, "posicion": "arriba|abajo|ambos", "alto_cm": 2.5},
      "marca_agua": {"imagen": ref | "texto": "CONFIDENCIAL", "opacidad": 0.15, "tamano": 0.6, "rotacion": 45, "color": "#8A94A6"},
      "colores":    {"titulos": "#0B4F8A", "acento": "#0094FF", "tabla_encabezado": "#0B4F8A", "tabla_texto": "#FFFFFF", "texto": "#1C2B4B"},
      "fuente":     "Arial",
    }

`ref` es lo que cada quien sabe resolver (un adjunto del chat por nombre, un archivo
del flujo, una variable, un recurso de la biblioteca «plantilla/recurso»): `cargar`
recibe la función que trae sus bytes. La opacidad es de verdad: en PDF con un
ExtGState (/ca) del generador propio (documentos._PDF), en PowerPoint con
alphaModFix y en Word con la imagen ya aclarada (Pillow). Licencias: Pillow (HPND),
python-docx y python-pptx (MIT), pypdfium2 (Apache-2.0 / BSD-3-Clause).
"""
from __future__ import annotations

import base64
import html as m_html
import io
import math
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

POSICIONES_LOGO = ("izquierda", "centro", "derecha")
POSICIONES_BANNER = ("arriba", "abajo", "ambos")
COLORES = ("titulos", "acento", "tabla_encabezado", "tabla_texto", "texto")
CLAVES = ("logo", "banner", "marca_agua", "colores", "fuente")
FORMATOS = ("pdf", "docx", "pptx", "html", "png", "jpg")
HEX = re.compile(r"^#?([0-9A-Fa-f]{6})$")
EXT_IMAGEN = re.compile(r"\.(png|jpe?g|webp|gif|bmp|tiff?)$", re.I)
MAX_LADO = 2400
CM = 360000  # EMU por centímetro (Word y PowerPoint)
PT_CM = 72 / 2.54


class ErrorDeMarca(ValueError):
    pass


# =====================================================================
#   la config: normalizar y validar
# =====================================================================

def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip().replace(",", ".").rstrip("%").strip())
        except ValueError:
            return None
    return None


def _hex(v: Any) -> Optional[str]:
    m = HEX.match(str(v or "").strip())
    return m.group(1).upper() if m else None


def _es_plantilla(v: Any) -> bool:
    return isinstance(v, str) and "{{" in v


def _parece_imagen(v: str) -> bool:
    s = v.strip()
    return bool(EXT_IMAGEN.search(s) or s.startswith(("a-", "{{")) or "/" in s or s.lower() in ("ultimo", "último", "ultima", "última"))


def opacidad(v: Any, defecto: float = 0.15) -> float:
    """0.15 o 15 (%) → 0.15."""
    x = _num(v)
    if x is None or x <= 0:
        return defecto
    return max(0.01, min(1.0, x / 100 if x > 1 else x))


def _fraccion(v: Any, defecto: float) -> float:
    x = _num(v)
    if x is None or x <= 0:
        return defecto
    return max(0.05, min(1.0, x / 100 if x > 1 else x))


def normalizar(m: Any) -> Dict[str, Any]:
    """La marca con sus valores por defecto; lo que no viene, no está. Acepta atajos: «logo»: "logo.png"."""
    if not isinstance(m, dict):
        return {}
    r: Dict[str, Any] = {}
    for clave in ("logo", "banner"):
        v = m.get(clave)
        if isinstance(v, str) and v.strip():
            v = {"imagen": v}
        if isinstance(v, dict) and str(v.get("imagen") or "").strip():
            if clave == "logo":
                pos = str(v.get("posicion") or "derecha").lower()
                r["logo"] = {"imagen": str(v["imagen"]).strip(), "posicion": pos if pos in POSICIONES_LOGO else "derecha",
                             "ancho_cm": max(0.5, min(25.0, _num(v.get("ancho_cm")) or 3.5))}
            else:
                pos = str(v.get("posicion") or "arriba").lower()
                alto = _num(v.get("alto_cm"))
                r["banner"] = {"imagen": str(v["imagen"]).strip(), "posicion": pos if pos in POSICIONES_BANNER else "arriba",
                               "alto_cm": max(0.3, min(15.0, alto)) if alto else None}
    v = m.get("marca_agua")
    if isinstance(v, str) and v.strip():
        v = {"imagen": v} if _parece_imagen(v) else {"texto": v}
    if isinstance(v, dict) and (str(v.get("imagen") or "").strip() or str(v.get("texto") or "").strip()):
        es_texto = not str(v.get("imagen") or "").strip()
        rot = _num(v.get("rotacion"))
        x = {"opacidad": opacidad(v.get("opacidad")), "tamano": _fraccion(v.get("tamano"), 0.7 if es_texto else 0.6),
             "rotacion": max(-360.0, min(360.0, rot if rot is not None else (45.0 if es_texto else 0.0)))}
        if es_texto:
            x.update(texto=str(v["texto"]).strip()[:80], color=_hex(v.get("color")) or "8A94A6")
        else:
            x["imagen"] = str(v["imagen"]).strip()
        r["marca_agua"] = x
    c = m.get("colores")
    if isinstance(c, dict):
        colores = {k: _hex(c.get(k)) for k in COLORES if _hex(c.get(k))}
        if colores:
            r["colores"] = colores
    if isinstance(m.get("fuente"), str) and m["fuente"].strip():
        r["fuente"] = re.sub(r"[^\w .-]", "", m["fuente"]).strip()[:60]
    return r


def problemas(m: Any, q: Callable[[str], None], clave: str = "marca") -> None:
    """Lo que está mal en una config de marca (mensajes para la persona o para Vera)."""
    if m is None:
        return
    if not isinstance(m, dict):
        q(f"{clave} tiene que ser un objeto {{logo, banner, marca_agua, colores, fuente}}")
        return
    sobran = [k for k in m if k not in CLAVES]
    if sobran:
        q(f"{clave}: no conozco {', '.join(sobran)} (válidos: {', '.join(CLAVES)})")
    if not any(m.get(k) not in (None, "", {}) for k in CLAVES):
        q(f"{clave} está vacío: pon al menos logo, banner, marca_agua, colores o fuente")

    def numero(d: dict, k: str, que: str, minimo: float, maximo: float) -> None:
        if k in d and d[k] is not None and not _es_plantilla(d[k]):
            x = d[k] if isinstance(d[k], (int, float)) and not isinstance(d[k], bool) else None
            if x is None or not (minimo <= x <= maximo):
                q(f"{que}.{k} tiene que ser un número entre {minimo:g} y {maximo:g}")

    def imagen(d: dict, que: str, obligatoria: bool = True) -> None:
        v = d.get("imagen")
        if v is None and not obligatoria:
            return
        if not (isinstance(v, str) and v.strip()):
            q(f"{que}.imagen: la imagen (un adjunto del chat por nombre, un archivo del flujo «{{{{logo}}}}» o «a-…», o un recurso de Documentos «plantilla/logo»)")

    def opcion(d: dict, k: str, que: str, validos: Tuple[str, ...]) -> None:
        if k in d and d[k] is not None and not _es_plantilla(d[k]) and str(d[k]).lower() not in validos:
            q(f"{que}.{k} «{d[k]}» (válidos: {', '.join(validos)})")

    def solo(d: dict, que: str, permitidas: Tuple[str, ...]) -> None:
        extra = [k for k in d if k not in permitidas]
        if extra:
            q(f"{que}: no conozco {', '.join(extra)} (válidos: {', '.join(permitidas)})")

    for k, permitidas, posiciones in (("logo", ("imagen", "posicion", "ancho_cm"), POSICIONES_LOGO),
                                      ("banner", ("imagen", "posicion", "alto_cm"), POSICIONES_BANNER)):
        v = m.get(k)
        if v is None:
            continue
        que = f"{clave}.{k}"
        if isinstance(v, str):
            if not v.strip():
                q(f"{que} está vacío")
            continue
        if not isinstance(v, dict):
            q(f"{que} tiene que ser la imagen o un objeto {{{', '.join(permitidas)}}}")
            continue
        solo(v, que, permitidas)
        imagen(v, que)
        opcion(v, "posicion", que, posiciones)
        numero(v, "ancho_cm" if k == "logo" else "alto_cm", que, 0.5 if k == "logo" else 0.3, 25 if k == "logo" else 15)
    v = m.get("marca_agua")
    if v is not None:
        que = f"{clave}.marca_agua"
        if isinstance(v, str):
            if not v.strip():
                q(f"{que} está vacío")
        elif not isinstance(v, dict):
            q(f"{que} tiene que ser un objeto {{imagen o texto, opacidad, tamano, rotacion, color}}")
        else:
            solo(v, que, ("imagen", "texto", "opacidad", "tamano", "rotacion", "color"))
            tiene_img = isinstance(v.get("imagen"), str) and v["imagen"].strip()
            tiene_txt = isinstance(v.get("texto"), str) and v["texto"].strip()
            if bool(tiene_img) == bool(tiene_txt):
                q(f"{que} necesita imagen o texto (uno de los dos)")
            numero(v, "opacidad", que, 0.01, 100)
            numero(v, "tamano", que, 0.05, 100)
            numero(v, "rotacion", que, -360, 360)
            if "color" in v and v["color"] is not None and not _es_plantilla(v["color"]) and not _hex(v["color"]):
                q(f"{que}.color «{v['color']}»: un color en hexadecimal, p. ej. «#8A94A6»")
    c = m.get("colores")
    if c is not None:
        if not isinstance(c, dict):
            q(f"{clave}.colores tiene que ser un objeto {{{', '.join(COLORES)}}}")
        else:
            solo(c, f"{clave}.colores", COLORES)
            for k2, x in c.items():
                if k2 in COLORES and x is not None and not _es_plantilla(x) and not _hex(x):
                    q(f"{clave}.colores.{k2} «{x}»: un color en hexadecimal, p. ej. «#0B4F8A»")
    if "fuente" in m and m["fuente"] is not None and not (isinstance(m["fuente"], str) and 0 < len(m["fuente"].strip()) <= 60):
        q(f"{clave}.fuente: el nombre de una fuente, p. ej. «Arial» o «Calibri»")


def imagenes(m: Any) -> List[str]:
    """Las referencias de imagen que pide una marca (para resolverlas o revisarlas)."""
    n = normalizar(m)
    return [n[k]["imagen"] for k in ("logo", "banner", "marca_agua") if k in n and n[k].get("imagen")]


# =====================================================================
#   las imágenes (Pillow)
# =====================================================================

def _abrir(datos: bytes) -> Any:
    from PIL import Image
    img = Image.open(io.BytesIO(datos))
    img.load()
    img = img.convert("RGBA")
    if max(img.size) > MAX_LADO:
        img.thumbnail((MAX_LADO, MAX_LADO))
    return img


def _png(img: Any) -> bytes:
    s = io.BytesIO()
    img.save(s, format="PNG", optimize=True)
    return s.getvalue()


def _con_alfa(img: Any, op: float) -> Any:
    from PIL import Image
    a = img.getchannel("A").point(lambda x: int(x * op))
    r = img.copy()
    r.putalpha(a)
    return r if isinstance(r, Image.Image) else img


def _rotar(img: Any, grados: float) -> Any:
    from PIL import Image
    if not grados % 360:
        return img
    return img.rotate(grados, resample=Image.BICUBIC, expand=True)


def _fuente(tam: int) -> Any:
    from PIL import ImageFont
    for nombre in ("DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf"):
        try:
            return ImageFont.truetype(nombre, tam)
        except OSError:
            continue
    return ImageFont.load_default(size=tam)


def imagen_de_texto(texto: str, color: str, alto: int = 200) -> Any:
    """El texto de una marca de agua como imagen RGBA (opaca; la opacidad y el giro van aparte)."""
    from PIL import Image, ImageDraw
    f = _fuente(alto)
    caja = ImageDraw.Draw(Image.new("RGBA", (1, 1))).textbbox((0, 0), texto, font=f)
    w, h = caja[2] - caja[0] + alto // 3, caja[3] - caja[1] + alto // 3
    img = Image.new("RGBA", (max(1, w), max(1, h)), (0, 0, 0, 0))
    rgb = tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))
    ImageDraw.Draw(img).text((alto // 6 - caja[0], alto // 6 - caja[1]), texto, font=f, fill=rgb + (255,))
    return img


def cargar(m: Any, resolver: Callable[[str], Optional[bytes]]) -> Tuple[Dict[str, Any], List[str]]:
    """(marca lista para aplicar, referencias que no se encontraron o no son imagen). Cada imagen queda con su PNG y medidas."""
    n = normalizar(m)
    faltan: List[str] = []
    for k in ("logo", "banner", "marca_agua"):
        x = n.get(k)
        if not x:
            continue
        if x.get("imagen"):
            datos = None
            try:
                datos = resolver(x["imagen"])
            except Exception:  # noqa: BLE001 - una imagen que no está se informa, no tumba el documento
                datos = None
            try:
                img = _abrir(datos) if datos else None
            except Exception:  # noqa: BLE001
                img = None
            if img is None:
                faltan.append(x["imagen"])
                n.pop(k)
                continue
        else:
            img = imagen_de_texto(x["texto"], x["color"])
        if k == "marca_agua":
            img = _rotar(img, x["rotacion"])
            x["png_claro"] = _png(_con_alfa(img, x["opacidad"]))
        x["img"] = img
        x["png"] = _png(img)
        x["px"] = img.size
    return n, faltan


def hay_algo(n: Dict[str, Any]) -> bool:
    return any(k in n for k in CLAVES)


def resumen(n: Dict[str, Any]) -> str:
    partes = []
    if "logo" in n:
        partes.append(f"logo a la {n['logo']['posicion']}" if n["logo"]["posicion"] != "centro" else "logo al centro")
    if "banner" in n:
        partes.append({"arriba": "banner arriba", "abajo": "banner abajo", "ambos": "banner arriba y abajo"}[n["banner"]["posicion"]])
    if "marca_agua" in n:
        w = n["marca_agua"]
        partes.append(f"marca de agua {'«' + w['texto'] + '»' if w.get('texto') else 'con imagen'} al {round(w['opacidad'] * 100)} %")
    if "colores" in n:
        partes.append("colores")
    if "fuente" in n:
        partes.append(f"fuente {n['fuente']}")
    return ", ".join(partes)


# =====================================================================
#   aplicar a un documento que ya existe
# =====================================================================

def formato_de(nombre: str, datos: bytes = b"") -> str:
    if datos[:5] == b"%PDF-":
        return "pdf"
    if datos[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if datos[:3] == b"\xff\xd8\xff":
        return "jpg"
    e = (nombre or "").lower().rsplit(".", 1)[-1] if "." in (nombre or "") else ""
    return {"jpeg": "jpg", "htm": "html"}.get(e, e)


def aplicar(formato: str, datos: bytes, n: Dict[str, Any]) -> bytes:
    """El documento con la marca puesta, en su mismo formato. ErrorDeMarca si el formato no la admite."""
    f = {"jpeg": "jpg", "htm": "html"}.get((formato or "").lower().lstrip("."), (formato or "").lower().lstrip("."))
    if not hay_algo(n):
        return datos
    if f == "docx":
        return docx(datos, n)
    if f == "pptx":
        return pptx(datos, n)
    if f == "pdf":
        return pdf(datos, n)
    if f == "html":
        return html(datos, n)
    if f in ("png", "jpg"):
        return imagen(datos, n, f)
    raise ErrorDeMarca(f"la marca se pone en PDF, Word, PowerPoint, HTML e imágenes PNG o JPG (no en {f or 'este formato'})")


# -------------------------------------------------------------------- Word

def _quitar_tema(el: Any, *atributos: str) -> None:
    from docx.oxml.ns import qn
    for a in atributos:
        el.attrib.pop(qn(a), None)


def _color_estilo_docx(estilo: Any, hexa: str) -> None:
    from docx.oxml.ns import qn
    from docx.shared import RGBColor
    estilo.font.color.rgb = RGBColor.from_string(hexa)
    c = estilo.element.rPr.find(qn("w:color"))
    if c is not None:
        _quitar_tema(c, "w:themeColor", "w:themeShade", "w:themeTint")


def _fuente_estilo_docx(estilo: Any, nombre: str) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    rpr = estilo.element.get_or_add_rPr()
    rf = rpr.find(qn("w:rFonts"))
    if rf is None:
        rf = OxmlElement("w:rFonts")
        rpr.insert(0, rf)
    for a in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        rf.set(qn(a), nombre)
    _quitar_tema(rf, "w:asciiTheme", "w:hAnsiTheme", "w:cstheme", "w:eastAsiaTheme")


def _borde_estilo_docx(estilo: Any, hexa: str) -> None:
    from docx.oxml import parse_xml
    from docx.oxml.ns import nsdecls, qn
    ppr = estilo.element.get_or_add_pPr()
    for viejo in ppr.findall(qn("w:pBdr")):
        ppr.remove(viejo)
    ppr.append(parse_xml(f'<w:pBdr {nsdecls("w")}><w:bottom w:val="single" w:sz="12" w:space="4" w:color="{hexa}"/></w:pBdr>'))


def _estilos_docx(d: Any, n: Dict[str, Any]) -> None:
    colores = n.get("colores") or {}
    estilos = {s.name: s for s in d.styles if getattr(s, "type", None) == 1}  # párrafo
    titulos = [estilos[x] for x in ("Title", "Heading 1", "Heading 2", "Heading 3", "Heading 4") if x in estilos]
    if colores.get("titulos"):
        for s in titulos:
            _color_estilo_docx(s, colores["titulos"])
    if colores.get("acento"):
        for x in ("Title", "Heading 1"):
            if x in estilos:
                _borde_estilo_docx(estilos[x], colores["acento"])
    if colores.get("texto") and "Normal" in estilos:
        _color_estilo_docx(estilos["Normal"], colores["texto"])
    if n.get("fuente"):
        for s in [estilos[x] for x in ("Normal",) if x in estilos] + titulos:
            _fuente_estilo_docx(s, n["fuente"])
    if colores.get("tabla_encabezado"):
        from docx.oxml import parse_xml
        from docx.oxml.ns import nsdecls, qn
        from docx.shared import RGBColor
        letra = RGBColor.from_string(colores.get("tabla_texto") or "FFFFFF")
        for t in d.tables:
            if not t.rows:
                continue
            for celda in t.rows[0].cells:
                tcpr = celda._tc.get_or_add_tcPr()
                for viejo in tcpr.findall(qn("w:shd")):
                    tcpr.remove(viejo)
                tcpr.append(parse_xml(f'<w:shd {nsdecls("w")} w:val="clear" w:color="auto" w:fill="{colores["tabla_encabezado"]}"/>'))
                for p in celda.paragraphs:
                    for r in p.runs:
                        r.font.color.rgb = letra
                        r.font.bold = True


def _anclar_docx(run: Any, nombre: str = "Marca de agua") -> None:
    """Pasa la imagen en línea del run a una flotante detrás del texto, centrada en la página."""
    from docx.oxml import parse_xml
    from docx.oxml.ns import nsdecls, qn
    inline = run._r.find(".//" + qn("wp:inline"))
    if inline is None:
        return
    ext = inline.find(qn("wp:extent"))
    pr = inline.find(qn("wp:docPr"))
    grafico = inline.find(qn("a:graphic"))
    ancla = parse_xml(
        f'<wp:anchor {nsdecls("wp", "a", "r")} distT="0" distB="0" distL="0" distR="0" simplePos="0" relativeHeight="0" behindDoc="1" '
        'locked="0" layoutInCell="1" allowOverlap="1"><wp:simplePos x="0" y="0"/>'
        '<wp:positionH relativeFrom="page"><wp:align>center</wp:align></wp:positionH>'
        '<wp:positionV relativeFrom="page"><wp:align>center</wp:align></wp:positionV>'
        f'<wp:extent cx="{ext.get("cx")}" cy="{ext.get("cy")}"/><wp:effectExtent l="0" t="0" r="0" b="0"/><wp:wrapNone/>'
        f'<wp:docPr id="{pr.get("id")}" name="{m_html.escape(nombre)}"/><wp:cNvGraphicFramePr/></wp:anchor>')
    ancla.append(grafico)
    inline.getparent().replace(inline, ancla)


def _ancho_imagen(px: Tuple[int, int], ancho: int, alto_max: Optional[int] = None) -> Tuple[int, int]:
    w, h = px
    aw, ah = ancho, int(ancho * h / max(1, w))
    if alto_max and ah > alto_max:
        ah, aw = alto_max, int(alto_max * w / max(1, h))
    return aw, ah


def docx(datos: bytes, n: Dict[str, Any]) -> bytes:
    import docx as m_docx
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Emu

    d = m_docx.Document(io.BytesIO(datos))
    _estilos_docx(d, n)
    alinear = {"izquierda": WD_ALIGN_PARAGRAPH.LEFT, "centro": WD_ALIGN_PARAGRAPH.CENTER, "derecha": WD_ALIGN_PARAGRAPH.RIGHT}
    banner, logo, agua = n.get("banner"), n.get("logo"), n.get("marca_agua")
    for s in d.sections:
        util = int(s.page_width - s.left_margin - s.right_margin)
        if banner or logo or agua:
            s.header.is_linked_to_previous = False
            cab = s.header
            primero = cab.paragraphs[0] if cab.paragraphs else cab.add_paragraph()
            vacio = not primero.text.strip() and not primero._p.findall(".//{*}drawing")
            nuevos = []
            if banner and banner["posicion"] in ("arriba", "ambos"):
                nuevos.append(("banner", banner))
            if logo:
                nuevos.append(("logo", logo))
            for tipo, x in nuevos:
                p = primero.insert_paragraph_before()
                p.paragraph_format.space_after = Emu(0)
                if tipo == "banner":
                    w, h = _ancho_imagen(x["px"], util, int(x["alto_cm"] * CM) if x.get("alto_cm") else int(4 * CM))
                    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                else:
                    w, h = _ancho_imagen(x["px"], int(x["ancho_cm"] * CM), int(3.5 * CM))
                    p.alignment = alinear[x["posicion"]]
                p.add_run().add_picture(io.BytesIO(x["png"]), width=Emu(w), height=Emu(h))
            if agua:
                p = primero.insert_paragraph_before() if not vacio else primero
                ancho = int(s.page_width * agua["tamano"])
                w, h = _ancho_imagen(agua["px"], ancho, int(s.page_height * 0.9))
                r = p.add_run()
                r.add_picture(io.BytesIO(agua["png_claro"]), width=Emu(w), height=Emu(h))
                _anclar_docx(r)
            elif vacio and nuevos:
                primero._p.getparent().remove(primero._p)
        if banner and banner["posicion"] in ("abajo", "ambos"):
            s.footer.is_linked_to_previous = False
            pie = s.footer
            ultimo = pie.paragraphs[-1] if pie.paragraphs else pie.add_paragraph()
            p = ultimo if not ultimo.text.strip() and not ultimo._p.findall(".//{*}drawing") else pie.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            w, h = _ancho_imagen(banner["px"], util, int(banner["alto_cm"] * CM) if banner.get("alto_cm") else int(4 * CM))
            p.add_run().add_picture(io.BytesIO(banner["png"]), width=Emu(w), height=Emu(h))
    s_ = io.BytesIO()
    d.save(s_)
    return s_.getvalue()


# -------------------------------------------------------------- PowerPoint

def diseno_pptx(W: int, H: int, n: Dict[str, Any]) -> Dict[str, Any]:
    """Dónde va cada pieza en una diapositiva de W×H EMU, y cuánto espacio le quitan al contenido."""
    r: Dict[str, Any] = {"arriba": 0, "abajo": 0, "titulo_izq": 0, "titulo_der": 0}
    b = n.get("banner")
    if b:
        tope = int(b["alto_cm"] * CM) if b.get("alto_cm") else int(H * 0.12)
        w, h = _ancho_imagen(b["px"], W, tope)
        if b["posicion"] in ("arriba", "ambos"):
            r["banner_arriba"] = ((W - w) // 2, 0, w, h)
            r["arriba"] = h
        if b["posicion"] in ("abajo", "ambos"):
            r["banner_abajo"] = ((W - w) // 2, H - h, w, h)
            r["abajo"] = h
    lg = n.get("logo")
    if lg:
        margen = int(0.25 * 914400)
        w, h = _ancho_imagen(lg["px"], int(lg["ancho_cm"] * CM), int(H * 0.14))
        y = r["arriba"] + int(0.15 * 914400) if r["arriba"] else margen
        x = {"izquierda": margen, "centro": (W - w) // 2, "derecha": W - margen - w}[lg["posicion"]]
        r["logo"] = (x, y, w, h)
        if lg["posicion"] == "izquierda":
            r["titulo_izq"] = x + w
        elif lg["posicion"] == "derecha":
            r["titulo_der"] = W - x
        else:
            r["arriba"] = y + h
    a = n.get("marca_agua")
    if a:
        w, h = _ancho_imagen(a["px"], int(W * a["tamano"]), int(H * 0.9))
        r["marca_agua"] = ((W - w) // 2, (H - h) // 2, w, h)
    return r


def _alfa_pptx(pic: Any, op: float) -> None:
    from pptx.oxml.ns import qn
    blip = pic._element.find(".//" + qn("a:blip"))
    if blip is not None:
        for viejo in blip.findall(qn("a:alphaModFix")):
            blip.remove(viejo)
        el = blip.makeelement(qn("a:alphaModFix"), {"amt": str(int(op * 100000))})
        blip.insert(0, el)


def _al_fondo(slide: Any, forma: Any) -> None:
    arbol = slide.shapes._spTree
    arbol.remove(forma._element)
    arbol.insert(2, forma._element)  # después de nvGrpSpPr y grpSpPr: detrás de todo lo demás


def piezas_pptx(prs: Any, n: Dict[str, Any], diapositivas: Optional[List[Any]] = None) -> None:
    """Pone banner, logo y marca de agua en cada diapositiva (o en las dadas)."""
    from pptx.util import Emu
    W, H = int(prs.slide_width), int(prs.slide_height)
    g = diseno_pptx(W, H, n)
    for s in diapositivas if diapositivas is not None else list(prs.slides):
        a = n.get("marca_agua")
        if a:
            x, y, w, h = g["marca_agua"]
            pic = s.shapes.add_picture(io.BytesIO(a["png"]), Emu(x), Emu(y), Emu(w), Emu(h))
            pic.name = "Marca de agua"
            _alfa_pptx(pic, a["opacidad"])
            _al_fondo(s, pic)
        for clave, nombre in (("banner_arriba", "Banner"), ("banner_abajo", "Banner del pie")):
            if clave in g:
                x, y, w, h = g[clave]
                s.shapes.add_picture(io.BytesIO(n["banner"]["png"]), Emu(x), Emu(y), Emu(w), Emu(h)).name = nombre
        if "logo" in g:
            x, y, w, h = g["logo"]
            s.shapes.add_picture(io.BytesIO(n["logo"]["png"]), Emu(x), Emu(y), Emu(w), Emu(h)).name = "Logo"


def colores_pptx(prs: Any, n: Dict[str, Any]) -> None:
    """Colores de títulos y encabezado de tablas, y la fuente, en una presentación cualquiera."""
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE_TYPE, PP_PLACEHOLDER
    colores = n.get("colores") or {}
    fuente = n.get("fuente")
    if not colores and not fuente:
        return

    def formas(shapes: Any) -> Any:
        for f in shapes:
            if f.shape_type == MSO_SHAPE_TYPE.GROUP:
                yield from formas(f.shapes)
            else:
                yield f

    for s in prs.slides:
        for f in formas(s.shapes):
            es_titulo = f.is_placeholder and f.placeholder_format.type in (PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE)
            if f.has_text_frame:
                for p in f.text_frame.paragraphs:
                    for r in p.runs:
                        if fuente:
                            r.font.name = fuente
                        if es_titulo and colores.get("titulos"):
                            r.font.color.rgb = RGBColor.from_string(colores["titulos"])
                        elif not es_titulo and colores.get("texto"):
                            r.font.color.rgb = RGBColor.from_string(colores["texto"])
            if getattr(f, "has_table", False) and f.has_table:
                t = f.table
                for k, celda in enumerate(t.rows[0].cells if len(t.rows) else []):
                    if colores.get("tabla_encabezado"):
                        celda.fill.solid()
                        celda.fill.fore_color.rgb = RGBColor.from_string(colores["tabla_encabezado"])
                    for p in celda.text_frame.paragraphs:
                        for r in p.runs:
                            if colores.get("tabla_encabezado"):
                                r.font.color.rgb = RGBColor.from_string(colores.get("tabla_texto") or "FFFFFF")
                            if fuente:
                                r.font.name = fuente
                for fila in list(t.rows)[1:]:
                    for celda in fila.cells:
                        for p in celda.text_frame.paragraphs:
                            for r in p.runs:
                                if fuente:
                                    r.font.name = fuente


def pptx(datos: bytes, n: Dict[str, Any], con_colores: bool = True) -> bytes:
    from pptx import Presentation
    prs = Presentation(io.BytesIO(datos))
    if con_colores:
        colores_pptx(prs, n)
    piezas_pptx(prs, n)
    s = io.BytesIO()
    prs.save(s)
    return s.getvalue()


# --------------------------------------------------------------------- PDF

def pdf(datos: bytes, n: Dict[str, Any]) -> bytes:
    """Estampa la marca sobre cada página de un PDF que ya existe: arma con el generador propio una hoja
    transparente del tamaño de cada página (marca de agua con /ca, logo y banners) y la pone encima como XObject."""
    import pypdfium2 as pdfium
    import documentos as m_doc

    doc = pdfium.PdfDocument(datos)
    capas: Dict[Tuple[float, float], Tuple[Any, Any]] = {}
    try:
        for i in range(len(doc)):
            pag = doc[i]
            try:
                w, h = pag.get_size()
                izq, abajo, _, _ = pag.get_mediabox()
                clave = (round(w, 1), round(h, 1))
                if clave not in capas:
                    hoja = pdfium.PdfDocument(m_doc.pdf_de_bloques([], "", n, (w, h), solo_marca=True))
                    capas[clave] = (hoja, hoja.page_as_xobject(0, doc))
                obj = capas[clave][1].as_pageobject()
                if izq or abajo:
                    obj.transform(pdfium.PdfMatrix().translate(izq, abajo))
                pag.insert_obj(obj)
                pag.gen_content()
            finally:
                pag.close()
        s = io.BytesIO()
        doc.save(s)
        return s.getvalue()
    finally:
        for hoja, xo in capas.values():
            try:
                xo.close()
            except Exception:  # noqa: BLE001
                pass
            hoja.close()
        doc.close()


# -------------------------------------------------------------------- HTML

def _uri(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode()


def html(datos: bytes, n: Dict[str, Any]) -> bytes:
    for cod in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            texto = datos.decode(cod)
            break
        except UnicodeDecodeError:
            continue
    colores = n.get("colores") or {}
    css = []
    if colores.get("titulos"):
        css.append(f"h1,h2,h3,h4{{color:#{colores['titulos']}!important}}")
    if colores.get("acento"):
        css.append(f"h1{{border-bottom:3px solid #{colores['acento']};padding-bottom:6px}}")
    if colores.get("tabla_encabezado"):
        css.append(f"th{{background:#{colores['tabla_encabezado']}!important;color:#{colores.get('tabla_texto') or 'FFFFFF'}!important}}")
    if colores.get("texto"):
        css.append(f"body{{color:#{colores['texto']}}}")
    if n.get("fuente"):
        css.append(f"body,table{{font-family:\"{n['fuente']}\",Arial,Helvetica,sans-serif!important}}")
    arriba, abajo = [], []
    b = n.get("banner")
    if b and b["posicion"] in ("arriba", "ambos"):
        arriba.append(f'<div class="marca-banner"><img alt="" src="{_uri(b["png"])}" style="display:block;width:100%;height:auto"></div>')
    if b and b["posicion"] in ("abajo", "ambos"):
        abajo.append(f'<div class="marca-banner-pie"><img alt="" src="{_uri(b["png"])}" style="display:block;width:100%;height:auto"></div>')
    lg = n.get("logo")
    if lg:
        alinear = {"izquierda": "left", "centro": "center", "derecha": "right"}[lg["posicion"]]
        arriba.append(f'<div class="marca-logo" style="text-align:{alinear};padding:14px 18px 4px"><img alt="Logo" src="{_uri(lg["png"])}" '
                      f'style="width:{lg["ancho_cm"]:.1f}cm;max-width:40%;height:auto"></div>')
    a = n.get("marca_agua")
    if a:
        css.append(".marca-agua{position:fixed;inset:0;display:flex;align-items:center;justify-content:center;pointer-events:none;z-index:2147483000}")
        arriba.append(f'<div class="marca-agua" aria-hidden="true"><img alt="" src="{_uri(a["png_claro"])}" style="width:{a["tamano"] * 100:.0f}vw;height:auto"></div>')
    estilo = f"<style>{''.join(css)}</style>" if css else ""
    if estilo:
        texto = re.sub(r"</head\s*>", estilo + "</head>", texto, count=1, flags=re.I) if re.search(r"</head\s*>", texto, re.I) else estilo + texto
    if arriba:
        m = re.search(r"<body[^>]*>", texto, re.I)
        texto = texto[:m.end()] + "".join(arriba) + texto[m.end():] if m else "".join(arriba) + texto
    if abajo:
        texto = re.sub(r"</body\s*>", "".join(abajo) + "</body>", texto, count=1, flags=re.I) if re.search(r"</body\s*>", texto, re.I) else texto + "".join(abajo)
    return texto.encode("utf-8")


# ----------------------------------------------------------------- imágenes

def imagen(datos: bytes, n: Dict[str, Any], formato: str = "png") -> bytes:
    from PIL import Image
    base = Image.open(io.BytesIO(datos))
    base.load()
    base = base.convert("RGBA")
    W, H = base.size

    def pegar(img: Any, x: int, y: int) -> None:
        base.alpha_composite(img, (max(0, x), max(0, y)))

    a = n.get("marca_agua")
    if a:
        claro = Image.open(io.BytesIO(a["png_claro"])).convert("RGBA")
        w, h = _ancho_imagen(claro.size, int(W * a["tamano"]), int(H * 0.9))
        claro = claro.resize((max(1, w), max(1, h)), Image.LANCZOS)
        pegar(claro, (W - w) // 2, (H - h) // 2)
    b = n.get("banner")
    arriba = 0
    if b:
        tope = int(H * 0.16)
        w, h = _ancho_imagen(b["px"], W, tope)
        img = b["img"].resize((max(1, w), max(1, h)), Image.LANCZOS)
        if b["posicion"] in ("arriba", "ambos"):
            pegar(img, (W - w) // 2, 0)
            arriba = h
        if b["posicion"] in ("abajo", "ambos"):
            pegar(img, (W - w) // 2, H - h)
    lg = n.get("logo")
    if lg:
        w, h = _ancho_imagen(lg["px"], int(W * lg["ancho_cm"] / 21.0), int(H * 0.2))
        img = lg["img"].resize((max(1, w), max(1, h)), Image.LANCZOS)
        m = int(min(W, H) * 0.03)
        x = {"izquierda": m, "centro": (W - w) // 2, "derecha": W - m - w}[lg["posicion"]]
        pegar(img, x, arriba + m)
    s = io.BytesIO()
    if formato == "jpg":
        base.convert("RGB").save(s, format="JPEG", quality=90)
    else:
        base.save(s, format="PNG")
    return s.getvalue()


# =====================================================================
#   la ayuda que Vera consulta (no va en su prompt)
# =====================================================================

AYUDA: Dict[str, Any] = {
    "que_es": "La marca de un documento: logo en el encabezado, banner (franja arriba y/o abajo), marca de agua (imagen o texto, con "
              "opacidad, tamaño y rotación, centrada en cada página o diapositiva) y colores y fuente. Sirve en Word, PDF, PowerPoint, HTML e "
              "imágenes PNG/JPG (no en Excel ni CSV).",
    "opciones": {
        "logo": {"imagen": "la imagen (obligatoria)", "posicion": "izquierda | centro | derecha (por defecto derecha)",
                 "ancho_cm": "ancho del logo, 0.5 a 25 (por defecto 3.5)"},
        "banner": {"imagen": "la franja (obligatoria)", "posicion": "arriba | abajo | ambos (por defecto arriba)",
                   "alto_cm": "alto máximo, 0.3 a 15 (por defecto: el que dé su proporción, hasta ~4 cm en Word)"},
        "marca_agua": {"imagen o texto": "una imagen (p. ej. el logo) o un texto como «CONFIDENCIAL» o «BORRADOR» (uno de los dos)",
                       "opacidad": "0.01 a 1 o en % (15 = 15 %); por defecto 0.15", "tamano": "fracción del ancho de la página, 0.05 a 1 o en % (por defecto 0.6; 0.7 con texto)",
                       "rotacion": "grados, contra las manecillas (por defecto 0; 45 con texto)", "color": "solo texto: hexadecimal, por defecto #8A94A6"},
        "colores": {"titulos": "títulos y subtítulos", "acento": "línea bajo el título, barras y detalles", "tabla_encabezado": "fondo del encabezado de las tablas",
                    "tabla_texto": "letra del encabezado de las tablas (por defecto blanco)", "texto": "letra del cuerpo"},
        "fuente": "nombre de la fuente (Word, PowerPoint y HTML la usan tal cual; el PDF usa Helvetica, Times o Courier según se parezca)",
    },
    "colores_linktic": {"azul_marino": "#0E1C56", "azul": "#0094FF", "azul_hondo": "#015B9D", "azul_tinta": "#0067B8", "azul_documento": "#0B4F8A"},
    "imagenes": {
        "chat": "el nombre de un adjunto que mandó la persona («logo.png») o de un archivo que generaste en este chat; «ultimo» toma la primera "
                "imagen del último mensaje que trajo imágenes (si mandó varias, usa los nombres); «plantilla/recurso» toma un recurso de la "
                "biblioteca de Documentos (p. ej. «carta-respuesta/logo»; plantillas_documento lista sus recursos).",
        "flujo": "una variable con un archivo («{{logo}}», «{{entrada.logo}}»), el id de un archivo del flujo («a-…»; archivos_del_flujo) o un "
                 "recurso de la biblioteca «plantilla/recurso». En documento_plantilla, un nombre suelto («logo») es un recurso de esa plantilla.",
    },
    "formatos": {
        "pdf": "logo, banners y marca de agua en cada página; en un PDF generado aquí, además, colores y fuente (y el texto se corre para no quedar debajo del logo). "
               "En un PDF que ya existe se estampan encima (marca de agua translúcida); los colores de su texto no cambian.",
        "docx": "logo y banner en el encabezado (y el pie), marca de agua detrás del texto en cada página; colores de títulos, encabezado de tablas y fuente por estilos.",
        "pptx": "en cada diapositiva: logo, banners y marca de agua al fondo; colores de títulos, tablas y fuente.",
        "html": "logo y banner arriba, marca de agua fija al centro, colores y fuente con CSS.",
        "imagen": "PNG/JPG: logo, banners y marca de agua encima.",
    },
    "ejemplos_chat": [
        {"pedido": "Hazme un Word con el acta de hoy y el logo que te mandé arriba a la izquierda",
         "herramienta": "generar_documento", "argumentos": {"formato": "docx", "titulo": "Acta", "contenido": "# Acta…",
                                                            "marca": {"logo": {"imagen": "logo.png", "posicion": "izquierda"}}}},
        {"pedido": "Ponle a estos PDF la marca de agua «CONFIDENCIAL» al 15 %",
         "herramienta": "aplicar_marca", "argumentos": {"archivos": [], "marca": {"marca_agua": {"texto": "CONFIDENCIAL", "opacidad": 15}}}},
        {"pedido": "El PowerPoint con nuestro banner abajo, el logo de marca de agua al 10 % y títulos en azul",
         "herramienta": "generar_documento", "argumentos": {"formato": "pptx", "titulo": "Resultados", "contenido": "# Resultados\n\n## …",
                                                            "marca": {"banner": {"imagen": "banner.png", "posicion": "abajo"},
                                                                      "marca_agua": {"imagen": "logo.png", "opacidad": 0.1, "tamano": 0.5},
                                                                      "colores": {"titulos": "#0B4F8A", "acento": "#0094FF"}}}},
    ],
    "en_flujos": {
        "documento_generar / documento_plantilla": "config.marca con lo mismo: {\"marca\": {\"logo\": {\"imagen\": \"{{logo}}\", \"posicion\": \"derecha\"}, "
                                                    "\"marca_agua\": {\"texto\": \"BORRADOR\", \"opacidad\": 0.12}}}",
        "documento_marca": "el nodo para masivos: estampa la marca en todos los documentos que le lleguen (los generados en la corrida, los "
                           "subidos, los adjuntos del correo o un filtro), uno por uno, y deja la lista de archivos marcados. Ejemplo: "
                           "{\"archivos\": [{\"de\": \"archivos\", \"tipo\": \"pdf\"}], \"marca\": {\"logo\": \"carta-respuesta/logo\", "
                           "\"marca_agua\": {\"imagen\": \"carta-respuesta/logo\", \"opacidad\": 0.1}}, \"sufijo\": \"-marcado\"}. Pide su ficha con ayuda_nodo.",
    },
}
