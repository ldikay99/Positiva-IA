"""Extrae constantes de modulo (valor, comentario, uso) por AST. Solo lectura."""
import ast, os, sys, json, re as _re_real, io, tokenize

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "motor"))
SKIP = {"harness", "tests", "__pycache__", "docs", "orchestrator", "logging_system"}

FLAGS = {2: "IGNORECASE", 8: "MULTILINE", 16: "DOTALL", 64: "VERBOSE", 32: "UNICODE"}


class _Regex(dict):
    pass


class ReStub:
    IGNORECASE = I = 2
    MULTILINE = M = 8
    DOTALL = S = 16
    VERBOSE = X = 64
    UNICODE = U = 32

    @staticmethod
    def compile(p, f=0):
        r = _Regex()
        r["regex"] = p
        r["flags"] = "|".join(n for b, n in FLAGS.items() if f & b)
        return r

    @staticmethod
    def escape(s):
        return _re_real.escape(s)


def cargar_modulos():
    mods = {}
    for dp, dn, fn in os.walk(ROOT):
        dn[:] = [d for d in dn if d not in SKIP and not d.startswith('.')]
        for f in fn:
            if f.endswith('.py'):
                p = os.path.join(dp, f)
                rel = os.path.relpath(p, ROOT).replace('\\', '/')
                m = rel[:-3].replace('/', '.')
                if m.endswith('.__init__'):
                    m = m[:-9]
                mods[m] = (p, rel)
    return mods


def es_const(n):
    core = n.lstrip('_')
    return bool(core) and core.replace('_', '').isalnum() and core.isupper()


def comentario_previo(lines, lineno):
    """Bloque de comentarios inmediatamente encima de la linea (1-based)."""
    out = []
    i = lineno - 2
    while i >= 0:
        s = lines[i].strip()
        if s.startswith('#'):
            out.append(s.lstrip('#').strip())
            i -= 1
            continue
        break
    out.reverse()
    texto = " ".join(x for x in out if x)
    return texto


def a_json(v):
    if isinstance(v, _Regex):
        return dict(v)
    if isinstance(v, (frozenset, set)):
        try:
            return sorted(a_json(x) for x in v)
        except TypeError:
            return [a_json(x) for x in v]
    if isinstance(v, (list, tuple)):
        return [a_json(x) for x in v]
    if isinstance(v, dict):
        return {str(k): a_json(x) for k, x in v.items()}
    if isinstance(v, bytes):
        return repr(v)
    if isinstance(v, (int, float, str, bool)) or v is None:
        return v
    raise TypeError(type(v))


def tipo_de(v):
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        return "str"
    if isinstance(v, _Regex):
        return "regex"
    if isinstance(v, (list, tuple, set, frozenset)):
        return "list"
    if isinstance(v, dict):
        return "dict"
    return "str"


from enum import IntEnum


class TipoDocumento(IntEnum):
    CC = 1; CE = 2; CD = 3; PPT = 4; PE = 5; TI = 6; NP = 7; SE = 8; FI = 9; NI = 10; PA = 11; NA = 12


class TipoPqrd(IntEnum):
    SOLICITUD = 1; DERECHO_PETICION = 2; FELICITACION = 3; SUGERENCIA = 4; QUEJA = 5
    RECLAMO = 6; PQRD_EXPRESS = 7; DERECHO_PETICION_VERBAL = 8


class TipoSolicitud(IntEnum):
    ACTUALIZACION_DATOS = 1; OTRA = 14


class _Sev:
    FATAL = "fatal"; DEGRADA = "degrada"; ALTA = "alta"; MEDIA = "media"; BAJA = "baja"


def _clase(**k):
    return dict(k)


GLOBAL = {}


def evaluar(mods):
    res = {}
    for _pasada in range(3):
        for m, (p, rel) in mods.items():
            _evaluar_modulo(m, p, rel, res)
    return res


def _evaluar_modulo(m, p, rel, res):
    if True:
        src = open(p, encoding='utf-8').read()
        lines = src.splitlines()
        tree = ast.parse(src)
        ns = {"re": ReStub, "frozenset": frozenset, "tuple": tuple, "set": set,
              "dict": dict, "list": list, "min": min, "max": max, "int": int,
              "float": float, "str": str, "len": len, "range": range, "sorted": sorted,
              "TipoDocumento": TipoDocumento, "TipoPqrd": TipoPqrd,
              "TipoSolicitud": TipoSolicitud, "Severidad": _Sev, "Clase": _clase,
              "__builtins__": {"int": int, "str": str, "len": len, "range": range}}
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module:
                for a in node.names:
                    k = f"{node.module}.{a.name}"
                    if k in GLOBAL:
                        ns[a.asname or a.name] = GLOBAL[k]
        prev_end, prev_com = -10, ""
        for node in tree.body:
            com_nodo = comentario_previo(lines, node.lineno)
            if not com_nodo and node.lineno - 1 == prev_end:
                com_nodo = prev_com
            prev_end, prev_com = getattr(node, "end_lineno", node.lineno), com_nodo
            tg, val = [], None
            if isinstance(node, ast.Assign):
                tg = [t.id for t in node.targets if isinstance(t, ast.Name)]
                val = node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
                tg = [node.target.id]
                val = node.value
            if not tg:
                continue
            seg = ast.get_source_segment(src, val) or ""
            try:
                v = eval(compile(ast.Expression(val), "<c>", "eval"), ns)
                ok = True
            except Exception:
                v, ok = None, False
            for t in tg:
                if ok:
                    ns[t] = v
                    GLOBAL[f"{m}.{t}"] = v
                if not es_const(t):
                    continue
                entrada = {"modulo": m, "archivo": rel, "linea": node.lineno,
                           "comentario": com_nodo,
                           "fuente": seg}
                if ok:
                    try:
                        entrada["valor"] = a_json(v)
                        entrada["tipo"] = tipo_de(v)
                        entrada["evaluado"] = True
                    except TypeError:
                        entrada["valor"] = seg
                        entrada["tipo"] = "str"
                        entrada["evaluado"] = False
                else:
                    entrada["valor"] = seg
                    entrada["tipo"] = "expr"
                    entrada["evaluado"] = False
                res[f"{m}.{t}"] = entrada
    return res


def clasificar_usos(mods):
    trees = {m: ast.parse(open(p, encoding='utf-8').read()) for m, (p, _) in mods.items()}
    defs = {}
    for m, t in trees.items():
        for node in t.body:
            tg = []
            if isinstance(node, ast.Assign):
                tg = [x.id for x in node.targets if isinstance(x, ast.Name)]
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
                tg = [node.target.id]
            for x in tg:
                if es_const(x):
                    defs.setdefault(m, set()).add(x)

    def classify(tree, names):
        out = {n: set() for n in names}

        def visit(node, ctx, in_default=False):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                a = node.args
                for d in list(a.defaults) + [d for d in a.kw_defaults if d is not None]:
                    visit(d, ctx, True)
                for dec in getattr(node, 'decorator_list', []):
                    visit(dec, ctx)
                body = node.body if isinstance(node.body, list) else [node.body]
                for b in body:
                    visit(b, 'func')
                return
            if isinstance(node, ast.ClassDef):
                for b in node.body:
                    visit(b, ctx if ctx == 'func' else 'classbody')
                return
            if isinstance(node, ast.Name) and node.id in out and isinstance(node.ctx, ast.Load):
                out[node.id].add('default' if in_default else ctx)
            for c in ast.iter_child_nodes(node):
                visit(c, ctx, in_default)
        for n in tree.body:
            visit(n, 'module')
        return out

    res = {}
    for m, names in defs.items():
        u = classify(trees[m], names)
        for n in names:
            res[f"{m}.{n}"] = {"local": sorted(u[n]), "copias": [], "atributo_en": []}

    for m, t in trees.items():
        alias_mod = {}
        # imports a nivel de modulo vs dentro de funcion
        top_level_imports = set(id(n) for n in t.body if isinstance(n, ast.ImportFrom))
        for node in ast.walk(t):
            if isinstance(node, ast.ImportFrom) and node.module:
                base = node.module
                for a in node.names:
                    key = f"{base}.{a.name}"
                    local = a.asname or a.name
                    if key in res:
                        used = classify(t, [local])[local]
                        res[key]["copias"].append({
                            "en": m,
                            "import_a_nivel_de_modulo": id(node) in top_level_imports,
                            "usos_en_el_importador": sorted(used),
                        })
                    elif key in mods:
                        alias_mod[local] = key
            if isinstance(node, ast.Import):
                for a in node.names:
                    alias_mod[a.asname or a.name] = a.name
        for node in ast.walk(t):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in alias_mod:
                key = f"{alias_mod[node.value.id]}.{node.attr}"
                if key in res and m not in res[key]["atributo_en"]:
                    res[key]["atributo_en"].append(m)
    return res


def veredicto(clave, uso):
    """(sobrescribible, alcance, nota) automaticos."""
    mod, nombre = clave.rsplit('.', 1)
    vivos, congelados = [], []
    loc = uso["local"]
    if 'func' in loc:
        vivos.append(f"{mod} lo lee como global dentro de sus funciones en cada llamada")
    if 'default' in loc:
        congelados.append(f"en {mod} se usa como valor por defecto de un argumento (se fija al definir la funcion)")
    if 'module' in loc:
        congelados.append(f"en {mod} se usa al importar para construir otra constante")
    if 'classbody' in loc:
        congelados.append(f"en {mod} se usa como valor por defecto de un atributo de clase/dataclass (se fija al importar)")
    for c in uso["copias"]:
        if c["import_a_nivel_de_modulo"]:
            if c["usos_en_el_importador"]:
                congelados.append(
                    f"{c['en']} lo copia con `from {mod} import {nombre}` al importar "
                    f"(para cambiarlo ahi: setattr({c['en']}, '{nombre}', v))")
        else:
            vivos.append(f"{c['en']} lo re-importa dentro de una funcion en cada llamada")
    for a in uso["atributo_en"]:
        vivos.append(f"{a} lo lee como atributo `modulo.{nombre}` en tiempo de llamada")
    if not vivos and not congelados:
        return False, "ninguno", "No se lee en ningun punto del pipeline (sin efecto)."
    if vivos and not congelados:
        return True, "total", "Si: " + "; ".join(vivos) + "."
    if vivos and congelados:
        return False, "parcial", ("Parcial, se marca como no sobrescribible: " + "; ".join(vivos)
                                 + ". PERO " + "; ".join(congelados) + ".")
    return False, "ninguno", "No: " + "; ".join(congelados) + "."


if __name__ == "__main__":
    mods = cargar_modulos()
    vals = evaluar(mods)
    usos = clasificar_usos(mods)
    for k, v in vals.items():
        u = usos.get(k, {"local": [], "copias": [], "atributo_en": []})
        v["usos"] = u
        s, a, n = veredicto(k, u)
        v["auto_sobrescribible"], v["auto_alcance"], v["auto_nota"] = s, a, n
    json.dump(vals, open(sys.argv[1], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(len(vals), "constantes")
