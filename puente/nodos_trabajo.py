"""
Los nodos de la fase 3 de integraciones (plan 5d), trabajo y clientes:
jira_issues, trello_tarjetas, asana_tareas, notion_paginas, hubspot_crm,
salesforce_registros, zoho_crm y docusign_sobre. Un nodo por servicio con su
`operacion`; el HTTP (sin SDK) está en `conectores/trabajo.py` y la ficha en
`especificacion_trabajo.py`.

Como `nodos_externos`, este módulo no importa el motor: `nodos_integraciones`
le delega lo que el motor pregunta de estos tipos (salidas, lo que deja, lo que
lee, la validación y la prueba de la conexión) y su clase hereda de
`NodosTrabajo`.

Reglas de siempre: leer sale libre; todo lo que crea, cambia, comenta o envía
pasa por `_escribir` (sale solo si una persona habilitó la escritura de la
conexión; si no, queda armado y retenido). Nunca direcciones de Positiva ni los
buzones protegidos en lo que se escribe: si aparecen, el paso se detiene y la
escritura queda registrada como vetada.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from conectores import trabajo as c_tr
from conectores.base import ErrorDeNegocio, direccion_vetada, timeout_de
from especificacion_trabajo import (CREDENCIALES_DE, DISPARADORES, ESCRITURA, LECTURA, LISTAN, OPERACIONES,  # noqa: F401
                                    OPERADORES_HUBSPOT, POR_DEFECTO, TIPOS)

ni: Any = None  # nodos_integraciones (lo enlaza él mismo al importarse)
OBJETOS = ("datos", "propiedades", "campos_extra", "filtro")
FECHA = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def fd() -> Any:
    return ni.fd


def operacion(t: str, c: dict) -> str:
    return c.get("operacion") or POR_DEFECTO[t]


# =====================================================================
#   lo que el motor necesita saber de cada tipo
# =====================================================================

def salidas(t: str, c: dict) -> Tuple[List[str], List[str]]:
    return (["ok", "vacio"], []) if operacion(t, c) in LISTAN else (["ok"], [])


def produce(t: str, c: dict, nid: str) -> Set[str]:
    s = c.get("salida") or nid
    return {s} if isinstance(s, str) else set()


def referencias(t: str, c: dict, plantillas_en: Callable, ref_de_fuente: Callable) -> List[Tuple[str, Set[str]]]:
    refs: List[Tuple[str, Set[str]]] = [(r, set()) for r in plantillas_en(c)]
    if t == "docusign_sobre" and "documento" in c:
        refs += [(r, set()) for r in fd().np_.refs_archivos(c.get("documento"), ref_de_fuente)]
    for e in c.get("entradas") or []:
        if isinstance(e, str):
            refs.append((fd()._ref_limpia(e), set()))
    return refs


def _fijo(x: Any) -> bool:
    """Un texto sin plantillas (se puede revisar antes de correr)."""
    return isinstance(x, str) and bool(x.strip()) and "{{" not in x


def _correo_malo(x: Any) -> Optional[str]:
    if not _fijo(x):
        return None
    if not c_tr.CORREO_EXACTO.match(x.strip()):
        return f"«{x[:60]}» no es una dirección de correo"
    motivo = direccion_vetada(x.strip())
    return f"bloqueado por seguridad: {motivo}" if motivo else None


def validar(t: str, c: dict, q: Callable[[str], None]) -> None:
    op = operacion(t, c)
    vacio = ni._vacio
    for k in OBJETOS:
        if k in c and c[k] is not None and not isinstance(c[k], dict):
            q(f"{k} tiene que ser un objeto JSON {{…}}")

    def falta(k: str, que: str) -> None:
        if vacio(c.get(k)):
            q(f"{t} {op} necesita {k} ({que})")
    if t == "jira_issues":
        if op == "buscar":
            falta("jql", "la consulta JQL")
        if op in ("ver", "comentar", "cambiar_estado"):
            falta("issue", "la clave, p. ej. SOP-123")
            if _fijo(c.get("issue")) and not c_tr.CLAVE_JIRA.match(c["issue"].strip().upper()):
                q(f"issue «{c['issue'][:40]}» no es una clave de Jira (PROYECTO-123)")
        if op == "crear":
            falta("proyecto", "la clave del proyecto")
            falta("resumen", "el título")
        if op == "comentar":
            falta("comentario", "el texto")
        if op == "cambiar_estado":
            falta("estado", "la transición o el estado destino")
        if isinstance(c.get("etiquetas"), list) and any(isinstance(x, str) and " " in x.strip() for x in c["etiquetas"]):
            q("etiquetas de Jira van sin espacios")
    elif t == "trello_tarjetas":
        if op == "listas":
            falta("tablero", "el id o enlace del tablero")
        if op == "tarjetas" and vacio(c.get("tablero")) and vacio(c.get("lista")):
            q("trello_tarjetas tarjetas necesita lista o tablero")
        if op == "crear":
            falta("lista", "la lista donde queda")
            falta("nombre", "el título")
        if op == "mover":
            falta("tarjeta", "la tarjeta")
            falta("lista", "la lista destino")
        if op == "comentar":
            falta("tarjeta", "la tarjeta")
            falta("comentario", "el texto")
    elif t == "asana_tareas":
        if op == "listar" and vacio(c.get("proyecto")) and vacio(c.get("asignado")):
            q("asana_tareas listar necesita proyecto o asignado («me» o un correo)")
        if op in ("ver", "completar", "comentar"):
            falta("tarea", "el gid de la tarea")
        if op == "crear":
            falta("nombre", "el nombre de la tarea")
        if op == "comentar":
            falta("comentario", "el texto")
        if _fijo(c.get("vence")) and not FECHA.match(c["vence"].strip()):
            q("vence: AAAA-MM-DD")
    elif t == "notion_paginas":
        if op in ("consultar", "crear"):
            falta("base_datos", "el id o enlace de la base")
        if op in ("actualizar", "agregar_bloques"):
            falta("pagina", "el id o enlace de la página")
        if op in ("crear", "actualizar"):
            falta("propiedades", "{columna: valor}")
        if op == "agregar_bloques":
            falta("contenido", "el texto")
        if "orden" in c and not (isinstance(c["orden"], list) and all(isinstance(x, dict) for x in c["orden"])):
            q("orden: una lista de {property, direction}")
    elif t == "hubspot_crm":
        if op in ("ver", "actualizar", "nota"):
            falta("id", "el id del registro")
        if op in ("crear", "actualizar"):
            falta("datos", "{propiedad: valor}")
        if op == "nota":
            falta("nota", "el texto")
        f = c.get("filtros")
        if f is not None and not (isinstance(f, list) and all(isinstance(x, dict) and isinstance(x.get("propiedad"), str) and x["propiedad"].strip()
                                                              and x.get("operador", "EQ") in OPERADORES_HUBSPOT for x in f)):
            q(f"filtros: una lista de {{propiedad, operador ({', '.join(OPERADORES_HUBSPOT[:6])}…), valor}}")
    elif t == "salesforce_registros":
        if op == "consultar":
            falta("soql", "SELECT … FROM …")
            if isinstance(c.get("soql"), str) and c["soql"].strip():
                try:
                    c_tr.revisar_soql(c["soql"])
                except ErrorDeNegocio as e:
                    q(str(e))
        if op in ("ver", "crear", "actualizar"):
            falta("objeto", "Account, Contact, Case…")
        if op in ("ver", "actualizar"):
            falta("id", "el Id del registro")
        if op in ("crear", "actualizar"):
            falta("datos", "{Campo: valor}")
        if _fijo(c.get("objeto")) and not c_tr.OBJETO_SF.match(c["objeto"].strip()):
            q("objeto: el nombre de API del objeto (Account, Case, Algo__c)")
        if _fijo(c.get("id")) and not c_tr.ID_SF.match(c["id"].strip()):
            q("id: el Id de Salesforce (15 o 18 caracteres)")
    elif t == "zoho_crm":
        if op == "buscar" and all(vacio(c.get(k)) for k in ("criterio", "correo", "telefono", "texto")):
            q("zoho_crm buscar necesita criterio, correo, telefono o texto")
        if op in ("ver", "actualizar"):
            falta("id", "el id del registro")
        if op in ("crear", "actualizar"):
            falta("datos", "{Campo: valor}")
        if _fijo(c.get("modulo")) and not c_tr.MODULO_ZOHO.match(c["modulo"].strip()):
            q("modulo: el nombre de API del módulo (Leads, Contacts, Accounts, Deals…)")
    elif t == "docusign_sobre":
        if op == "enviar":
            falta("documento", "el PDF o Word, p. ej. «{{documento}}»")
            falta("asunto", "el asunto")
            falta("firmantes", "[{nombre, correo}]")
            fd().np_.problemas_archivos(c, "documento", q)
            for k in ("firmantes", "copias"):
                xs = c.get(k)
                if xs is None:
                    continue
                if not (isinstance(xs, list) and all(isinstance(x, dict) and isinstance(x.get("correo"), str) and x["correo"].strip() for x in xs)):
                    q(f"{k}: una lista de {{nombre, correo}}")
                    continue
                for x in xs:
                    m = _correo_malo(x["correo"])
                    if m:
                        q(f"{k}: {m}")
                    if x.get("orden") is not None and not fd()._entero(x["orden"], 1, 50):
                        q(f"{k}: orden tiene que ser un entero entre 1 y 50")
        if op in ("estado", "descargar"):
            falta("sobre", "el id del sobre")
            if _fijo(c.get("sobre")) and not c_tr.ID_DOCUSIGN.match(c["sobre"].strip()):
                q("sobre: el id del sobre de DocuSign (un UUID)")


# =====================================================================
#   los métodos (NodosIntegraciones hereda de aquí)
# =====================================================================

class NodosTrabajo:
    # ------------------------------------------------------------- utilidades
    def _ct(self, n: dict) -> Tuple[dict, dict, str, float]:
        """(config, credencial, operación, timeout) del nodo."""
        c = self._cfg(n)
        return c, self.cred(c.get("credencial"), CREDENCIALES_DE[n["tipo"]]), operacion(n["tipo"], c), timeout_de(c)

    def _escapado(self, texto: Any, escapar: Callable[[str], str]) -> str:
        """Una plantilla donde cada {{valor}} se escapa (para JQL, SOQL o criterios de Zoho)."""
        return fd().PLANTILLA.sub(lambda m: escapar(fd()._a_texto(self.resolver(m.group(1)))), str(texto or ""))

    def _lista_de(self, n: dict, items: List[dict], titulo: str, linea: Callable[[dict], str], truncado: bool = False) -> str:
        self.vars[self._sal(n)] = items
        if truncado:
            self.avisos.append(f"{n['id']}: hay más de {len(items)}; se trajeron los primeros (sube max si necesitas más)")
        fd()._paso(n["id"], f"{len(items)} {titulo}", "ok" if items else "info", "\n".join(f"· {linea(x)}" for x in items[:15]), total=len(items), truncado=truncado)
        return "ok" if items else "vacio"

    def _uno(self, n: dict, x: dict, titulo: str) -> str:
        self.vars[self._sal(n)] = x
        fd()._paso(n["id"], titulo, "ok", fd()._a_texto(x)[:700])
        return "ok"

    def _escribe(self, n: dict, sistema: str, que: str, destino: str, detalle: dict, cred: dict, hacer: Callable[[], dict], claves: Tuple[str, ...],
                 **extra: Any) -> str:
        """Una escritura: primero los bloqueos (nunca Positiva) sobre todo lo que iría; luego sale solo con permiso o queda retenida."""
        try:
            c_tr.revisar_datos(detalle, que)
        except ErrorDeNegocio as e:
            self._retener_vetado(n, sistema, que, destino, detalle, e)
            raise
        r = self._escribir(n, sistema, que, destino, detalle, cred, hacer)
        self._guardar_salida(n, r, claves, **extra)
        return "ok"

    def _mapa(self, c: dict, k: str) -> dict:
        v = self.render_profundo(c.get(k) or {})
        if not isinstance(v, dict):
            raise ErrorDeNegocio(f"{k} tiene que ser un objeto {{campo: valor}}")
        return v

    # ================================================================== Jira
    def jira_issues(self, n: dict) -> str:
        c, cred, op, t = self._ct(n)
        v = cred["valores"]
        if op == "buscar":
            jql = self._escapado(c.get("jql"), c_tr.escapar_comillas).strip()
            issues, trunc = self._r(n, lambda: c_tr.jira_buscar(v, jql, [str(x) for x in c.get("campos") or []], int(c.get("max") or 50), t), "Jira")
            return self._lista_de(n, issues, "issue(s) de Jira", lambda x: f"{x['clave']} [{x['estado']}] {x['resumen'][:90]}", trunc)
        clave = self.render(c.get("issue") or "").strip().upper()
        if op != "crear" and not c_tr.CLAVE_JIRA.match(clave):
            raise ErrorDeNegocio(f"issue «{clave[:40]}» no es una clave de Jira (PROYECTO-123)")
        if op == "ver":
            x = self._r(n, lambda: c_tr.jira_ver(v, clave, t), "Jira")
            return self._uno(n, x, f"{x['clave']} [{x['estado']}] {x['resumen'][:100]}")
        sitio = c_tr.jira_base(v)
        if op == "crear":
            campos: Dict[str, Any] = {"project": {"key": self.render(c.get("proyecto") or "").strip()},
                                      "issuetype": {"name": self.render(c.get("tipo_issue") or "Task").strip() or "Task"},
                                      "summary": self.render(c.get("resumen") or "").strip()[:255]}
            descripcion = self.render(c.get("descripcion") or "")
            if descripcion.strip():
                campos["description"] = c_tr.adf(descripcion)
            if self.render(c.get("prioridad") or "").strip():
                campos["priority"] = {"name": self.render(c["prioridad"]).strip()}
            etiquetas = [self.render(x).strip().replace(" ", "_") for x in c.get("etiquetas") or [] if self.render(x).strip()]
            if etiquetas:
                campos["labels"] = etiquetas
            if self.render(c.get("asignado") or "").strip():
                campos["assignee"] = {"accountId": self.render(c["asignado"]).strip()}
            campos.update(self._mapa(c, "campos_extra"))
            detalle = {"sitio": sitio, "proyecto": campos["project"]["key"], "tipo": campos["issuetype"]["name"], "resumen": campos["summary"],
                       "descripcion": descripcion[:4000], "campos": {k: x for k, x in campos.items() if k not in ("description",)}}
            return self._escribe(n, "jira", "crear issue", f"jira:{campos['project']['key']}", detalle, cred, lambda: c_tr.jira_crear(v, campos, t),
                                 ("clave", "id", "url"), resumen=campos["summary"])
        if op == "comentar":
            texto = self.render(c.get("comentario") or "")
            return self._escribe(n, "jira", "comentar issue", f"jira:{clave}", {"sitio": sitio, "issue": clave, "comentario": texto}, cred,
                                 lambda: c_tr.jira_comentar(v, clave, texto, t), ("id",), issue=clave)
        estado = self.render(c.get("estado") or "").strip()
        return self._escribe(n, "jira", "cambiar estado", f"jira:{clave}", {"sitio": sitio, "issue": clave, "estado": estado}, cred,
                             lambda: c_tr.jira_cambiar_estado(v, clave, estado, t), ("estado", "transicion"), issue=clave, pedido=estado)

    # ================================================================ Trello
    def trello_tarjetas(self, n: dict) -> str:
        c, cred, op, t = self._ct(n)
        v = cred["valores"]
        r = lambda k, que: c_tr.id_trello(self.render(c.get(k) or ""), que) if self.render(c.get(k) or "").strip() else ""  # noqa: E731
        maximo = int(c.get("max") or 100)
        if op in ("tableros", "listas", "tarjetas"):
            tablero, lista = r("tablero", "tablero"), r("lista", "lista") if op == "tarjetas" else ""
            xs = self._r(n, lambda: c_tr.trello_leer(v, op, tablero, lista, maximo, t), "Trello")
            linea = (lambda x: f"{x['nombre'][:90]} ({x['id']})") if op != "tarjetas" else (lambda x: f"{x['nombre'][:90]}{' · vence ' + x['vence'][:10] if x['vence'] else ''}")
            return self._lista_de(n, xs, f"{'tablero(s)' if op == 'tableros' else 'lista(s)' if op == 'listas' else 'tarjeta(s)'} de Trello", linea)
        posicion = "top" if c.get("posicion") == "arriba" else "bottom"
        if op == "crear":
            lista = r("lista", "lista")
            datos: Dict[str, Any] = {"name": self.render(c.get("nombre") or "").strip()[:16384], "desc": self.render(c.get("descripcion") or ""), "pos": posicion}
            if self.render(c.get("vence") or "").strip():
                datos["due"] = self.render(c["vence"]).strip()
            etiquetas = [c_tr.id_trello(self.render(x), "etiqueta") for x in c.get("etiquetas") or [] if self.render(x).strip()]
            if etiquetas:
                datos["idLabels"] = ",".join(etiquetas)
            return self._escribe(n, "trello", "crear tarjeta", f"trello:lista/{lista}", {"lista": lista, **datos}, cred,
                                 lambda: c_tr.trello_crear(v, lista, datos, t), ("id", "url"), nombre=datos["name"])
        tarjeta = r("tarjeta", "tarjeta")
        if op == "mover":
            lista = r("lista", "lista destino")
            return self._escribe(n, "trello", "mover tarjeta", f"trello:tarjeta/{tarjeta}", {"tarjeta": tarjeta, "lista_destino": lista, "posicion": posicion}, cred,
                                 lambda: c_tr.trello_mover(v, tarjeta, lista, posicion, t), ("id", "lista", "url"), lista_destino=lista)
        texto = self.render(c.get("comentario") or "")
        return self._escribe(n, "trello", "comentar tarjeta", f"trello:tarjeta/{tarjeta}", {"tarjeta": tarjeta, "comentario": texto}, cred,
                             lambda: c_tr.trello_comentar(v, tarjeta, texto, t), ("id",), tarjeta=tarjeta)

    # ================================================================= Asana
    def asana_tareas(self, n: dict) -> str:
        c, cred, op, t = self._ct(n)
        v = cred["valores"]
        g = lambda k, que: c_tr.gid(self.render(c.get(k) or ""), que) if self.render(c.get(k) or "").strip() else ""  # noqa: E731
        espacio = g("espacio", "espacio") or (c_tr.gid(v["espacio"], "espacio de la conexión") if str(v.get("espacio") or "").strip() else "")
        asignado = self.render(c.get("asignado") or "").strip()
        if asignado and asignado != "me":
            c_tr.revisar_correo(asignado, "asignado")
        if op == "listar":
            proyecto = g("proyecto", "proyecto")
            if not proyecto and not espacio:
                raise ErrorDeNegocio("asana_tareas listar por asignado necesita el espacio de trabajo (en el nodo o en la conexión)")
            xs, trunc = self._r(n, lambda: c_tr.asana_listar(v, proyecto, asignado, espacio, c.get("solo_pendientes", True) is not False, int(c.get("max") or 100), t), "Asana")
            return self._lista_de(n, xs, "tarea(s) de Asana", lambda x: f"{'[x] ' if x['completada'] else ''}{x['nombre'][:90]}{' · vence ' + x['vence'] if x['vence'] else ''}", trunc)
        if op == "crear":
            proyecto = g("proyecto", "proyecto")
            datos: Dict[str, Any] = {"name": self.render(c.get("nombre") or "").strip(), "notes": self.render(c.get("notas") or "")}
            if proyecto:
                datos["projects"] = [proyecto]
            elif espacio:
                datos["workspace"] = espacio
            else:
                raise ErrorDeNegocio("asana_tareas crear necesita proyecto o el espacio de trabajo (en el nodo o en la conexión)")
            if asignado:
                datos["assignee"] = asignado
            vence = self.render(c.get("vence") or "").strip()
            if vence:
                if not FECHA.match(vence[:10]):
                    raise ErrorDeNegocio(f"vence «{vence[:30]}» tiene que ser AAAA-MM-DD")
                datos["due_on"] = vence[:10]
            return self._escribe(n, "asana", "crear tarea", f"asana:{proyecto or espacio}", dict(datos), cred, lambda: c_tr.asana_crear(v, datos, t),
                                 ("id", "url"), nombre=datos["name"])
        tarea = g("tarea", "tarea")
        if op == "ver":
            x = self._r(n, lambda: c_tr.asana_ver(v, tarea, t), "Asana")
            return self._uno(n, x, f"{x['nombre'][:100]}{' (completada)' if x['completada'] else ''}")
        if op == "completar":
            return self._escribe(n, "asana", "completar tarea", f"asana:tarea/{tarea}", {"tarea": tarea, "completed": True}, cred,
                                 lambda: c_tr.asana_completar(v, tarea, t), ("id", "completada"), tarea=tarea)
        texto = self.render(c.get("comentario") or "")
        return self._escribe(n, "asana", "comentar tarea", f"asana:tarea/{tarea}", {"tarea": tarea, "comentario": texto}, cred,
                             lambda: c_tr.asana_comentar(v, tarea, texto, t), ("id",), tarea=tarea)

    # ================================================================ Notion
    def notion_paginas(self, n: dict) -> str:
        c, cred, op, t = self._ct(n)
        v = cred["valores"]
        if op == "consultar":
            base = c_tr.id_notion(self.render(c.get("base_datos") or ""), "base_datos")
            filtro = self.render_profundo(c.get("filtro")) if c.get("filtro") else None
            orden = self.render_profundo(c.get("orden") or [])
            xs, trunc = self._r(n, lambda: c_tr.notion_consultar(v, base, filtro, orden, int(c.get("max") or 100), t), "Notion")
            return self._lista_de(n, xs, "página(s) de Notion",
                                  lambda x: " · ".join(f"{k}: {fd()._a_texto(val)[:40]}" for k, val in list(x["propiedades"].items())[:4]), trunc)
        contenido = self.render(c.get("contenido") or "")
        if op == "crear":
            base = c_tr.id_notion(self.render(c.get("base_datos") or ""), "base_datos")
            props = self._mapa(c, "propiedades")
            detalle = {"base_datos": base, "propiedades": props, **({"contenido": contenido[:4000], "bloques": len(c_tr.bloques(contenido))} if contenido.strip() else {})}
            return self._escribe(n, "notion", "crear página", f"notion:base/{base}", detalle, cred, lambda: c_tr.notion_crear(v, base, props, contenido, t),
                                 ("id", "url", "bloques"))
        pagina = c_tr.id_notion(self.render(c.get("pagina") or ""), "pagina")
        if op == "actualizar":
            props = self._mapa(c, "propiedades")
            return self._escribe(n, "notion", "actualizar página", f"notion:pagina/{pagina}", {"pagina": pagina, "propiedades": props}, cred,
                                 lambda: c_tr.notion_actualizar(v, pagina, props, t), ("id", "url", "cambiadas"))
        hijos = c_tr.bloques(contenido)
        if not hijos:
            raise ErrorDeNegocio("agregar_bloques: el contenido quedó vacío al llenarlo")
        return self._escribe(n, "notion", "agregar contenido", f"notion:pagina/{pagina}", {"pagina": pagina, "contenido": contenido[:4000], "bloques": len(hijos)}, cred,
                             lambda: c_tr.notion_agregar(v, pagina, hijos, t), ("id", "bloques"))

    # =============================================================== HubSpot
    def hubspot_crm(self, n: dict) -> str:
        c, cred, op, t = self._ct(n)
        v = cred["valores"]
        objeto = c_tr.OBJETOS_HUBSPOT[c.get("objeto") or "contactos"]
        props = [str(x) for x in c.get("propiedades") or []]
        if op == "buscar":
            filtros = []
            for f in c.get("filtros") or []:
                x = {"propertyName": str(f["propiedad"]).strip(), "operator": f.get("operador") or "EQ"}
                if f.get("operador") not in ("HAS_PROPERTY", "NOT_HAS_PROPERTY"):
                    val = self.render_profundo(f.get("valor"))
                    x["values" if x["operator"] in ("IN", "NOT_IN") else "value"] = val if isinstance(val, list) else fd()._a_texto(val)
                filtros.append(x)
            texto = self.render(c.get("texto") or "").strip()
            xs, trunc = self._r(n, lambda: c_tr.hubspot_buscar(v, objeto, texto, filtros, props, int(c.get("max") or 50), t), "HubSpot")
            principal = {"contacts": ("email", "firstname", "lastname"), "companies": ("name", "domain"), "deals": ("dealname", "amount", "dealstage")}[objeto]
            return self._lista_de(n, xs, f"{c.get('objeto') or 'contactos'} de HubSpot", lambda x: " · ".join(str(x.get(k) or "") for k in principal), trunc)
        ident = self.render(c.get("id") or "").strip()
        if op == "ver":
            x = self._r(n, lambda: c_tr.hubspot_ver(v, objeto, ident, props, t), "HubSpot")
            return self._uno(n, x, f"{c.get('objeto') or 'contactos'} {x.get('id')}")
        if op == "nota":
            texto = self.render(c.get("nota") or "")
            momento = int(ni._reloj() * 1000)
            return self._escribe(n, "hubspot", "agregar nota", f"hubspot:{objeto}/{ident}", {"objeto": objeto, "id": ident, "nota": texto}, cred,
                                 lambda: c_tr.hubspot_nota(v, objeto, ident, texto, momento, t), ("id", "asociada_a"))
        datos = self._mapa(c, "datos")
        if op == "crear":
            return self._escribe(n, "hubspot", f"crear {c.get('objeto') or 'contactos'}", f"hubspot:{objeto}", {"objeto": objeto, "datos": datos}, cred,
                                 lambda: c_tr.hubspot_crear(v, objeto, datos, t), ("id",))
        return self._escribe(n, "hubspot", f"actualizar {c.get('objeto') or 'contactos'}", f"hubspot:{objeto}/{ident}", {"objeto": objeto, "id": ident, "datos": datos},
                             cred, lambda: c_tr.hubspot_actualizar(v, objeto, ident, datos, t), ("id",))

    # ============================================================ Salesforce
    def salesforce_registros(self, n: dict) -> str:
        c, cred, op, t = self._ct(n)
        v = cred["valores"]
        if op == "consultar":
            soql = c_tr.revisar_soql(self._escapado(c.get("soql"), c_tr.escapar_comillas))
            filas, total, trunc = self._r(n, lambda: c_tr.sf_consultar(v, soql, int(c.get("max") or 200), t), "Salesforce")
            self.vars[self._sal(n)] = {"filas": filas, "total": total, "truncado": trunc}
            if trunc:
                self.avisos.append(f"{n['id']}: la consulta tiene {total} filas; se trajeron {len(filas)}")
            fd()._paso(n["id"], f"{len(filas)} fila(s) de Salesforce" + (f" de {total}" if trunc else ""), "ok" if filas else "info",
                       fd()._a_texto(filas[:3])[:700], total=total)
            return "ok" if filas else "vacio"
        objeto = self.render(c.get("objeto") or "").strip()
        if not c_tr.OBJETO_SF.match(objeto):
            raise ErrorDeNegocio(f"objeto «{objeto[:40]}» no es un nombre de objeto de Salesforce")
        ident = self.render(c.get("id") or "").strip()
        if op in ("ver", "actualizar") and not c_tr.ID_SF.match(ident):
            raise ErrorDeNegocio(f"id «{ident[:30]}» no es un Id de Salesforce (15 o 18 caracteres)")
        if op == "ver":
            x = self._r(n, lambda: c_tr.sf_ver(v, objeto, ident, [str(k) for k in c.get("campos") or []], t), "Salesforce")
            return self._uno(n, x, f"{objeto} {ident}")
        datos = self._mapa(c, "datos")
        if op == "crear":
            return self._escribe(n, "salesforce", f"crear {objeto}", f"salesforce:{objeto}", {"objeto": objeto, "datos": datos}, cred,
                                 lambda: c_tr.sf_crear(v, objeto, datos, t), ("id",), objeto=objeto)
        return self._escribe(n, "salesforce", f"actualizar {objeto}", f"salesforce:{objeto}/{ident}", {"objeto": objeto, "id": ident, "datos": datos}, cred,
                             lambda: c_tr.sf_actualizar(v, objeto, ident, datos, t), ("id", "cambiados"), objeto=objeto)

    # ============================================================== Zoho CRM
    def zoho_crm(self, n: dict) -> str:
        c, cred, op, t = self._ct(n)
        v = cred["valores"]
        modulo = self.render(c.get("modulo") or "").strip()
        if not c_tr.MODULO_ZOHO.match(modulo):
            raise ErrorDeNegocio(f"modulo «{modulo[:40]}» no es un módulo de Zoho (Leads, Contacts, Accounts…)")
        if op == "buscar":
            if self._escapado(c.get("criterio"), c_tr.escapar_zoho).strip():
                criterio = {"criteria": self._escapado(c["criterio"], c_tr.escapar_zoho).strip()}
            else:
                k = next((k for k in ("correo", "telefono", "texto") if self.render(c.get(k) or "").strip()), "")
                if not k:
                    raise ErrorDeNegocio("zoho_crm buscar: criterio, correo, telefono y texto quedaron vacíos al llenarlos")
                criterio = {{"correo": "email", "telefono": "phone", "texto": "word"}[k]: self.render(c[k]).strip()}
            xs, trunc = self._r(n, lambda: c_tr.zoho_buscar(v, modulo, criterio, int(c.get("max") or 50), t), "Zoho CRM")
            return self._lista_de(n, xs, f"registro(s) de {modulo}",
                                  lambda x: " · ".join(str(x.get(k)) for k in ("Full_Name", "Account_Name", "Deal_Name", "Email", "id") if x.get(k))[:120], trunc)
        ident = self.render(c.get("id") or "").strip()
        if op in ("ver", "actualizar") and not re.fullmatch(r"\d{5,25}", ident):
            raise ErrorDeNegocio(f"id «{ident[:30]}» no es un id de Zoho (solo dígitos)")
        if op == "ver":
            x = self._r(n, lambda: c_tr.zoho_ver(v, modulo, ident, t), "Zoho CRM")
            self.vars[self._sal(n)] = x
            fd()._paso(n["id"], f"{modulo} {ident}: {'encontrado' if x else 'no existe'}", "ok" if x else "info", fd()._a_texto(x)[:700])
            return "ok"
        datos = self._mapa(c, "datos")
        if op == "crear":
            return self._escribe(n, "zoho_crm", f"crear en {modulo}", f"zoho:{modulo}", {"modulo": modulo, "datos": datos}, cred,
                                 lambda: c_tr.zoho_crear(v, modulo, datos, t), ("id", "estado"), modulo=modulo)
        return self._escribe(n, "zoho_crm", f"actualizar en {modulo}", f"zoho:{modulo}/{ident}", {"modulo": modulo, "id": ident, "datos": datos}, cred,
                             lambda: c_tr.zoho_actualizar(v, modulo, ident, datos, t), ("id", "estado"), modulo=modulo)

    # ============================================================== DocuSign
    def docusign_sobre(self, n: dict) -> str:
        c, cred, op, t = self._ct(n)
        v = cred["valores"]
        if op in ("estado", "descargar"):
            sobre = self.render(c.get("sobre") or "").strip()
            if not c_tr.ID_DOCUSIGN.match(sobre):
                raise ErrorDeNegocio(f"sobre «{sobre[:40]}» no es un id de sobre de DocuSign")
            if op == "estado":
                x = self._r(n, lambda: c_tr.docusign_estado(v, sobre, t), "DocuSign")
                self.vars[self._sal(n)] = x
                fd()._paso(n["id"], f"Sobre {x['estado']} · {x['firmados']} de {x['total_firmantes']} firmaron", "ok",
                           "\n".join(f"· {f['nombre']} <{f['correo']}>: {f['estado']}" for f in x["firmantes"]))
                return "ok"
            datos = self._r(n, lambda: c_tr.docusign_descargar(v, sobre, t), "DocuSign")
            nombre = (self.render(c.get("nombre") or "").strip() or f"firmado-{sobre[:8]}").replace("/", "-")
            ficha = self._guardar_archivo(nombre if nombre.lower().endswith(".pdf") else f"{nombre}.pdf", datos, "application/pdf", "docusign")
            self.vars[self._sal(n)] = {**ficha, "sobre": sobre}
            fd()._paso(n["id"], f"PDF firmado: {ficha['nombre']}", "ok", f"{len(datos):,} bytes · id {ficha['id']}", archivo=ficha["id"])
            return "ok"
        archivos, resumen = self._para_envio(c.get("documento"), "el sobre de DocuSign", max_archivos=1, max_mb=25)
        if not archivos:
            raise ErrorDeNegocio("docusign_sobre: el documento no está (revisa la referencia, p. ej. «{{documento}}»)")
        gente = lambda k: [{"nombre": self.render(x.get("nombre") or "").strip(), "correo": self.render(x.get("correo") or "").strip(),  # noqa: E731
                            **({"orden": x["orden"]} if x.get("orden") else {})} for x in c.get(k) or [] if isinstance(x, dict)]
        firmantes, copias = gente("firmantes"), gente("copias")
        asunto, mensaje = self.render(c.get("asunto") or "").strip(), self.render(c.get("mensaje") or "")
        borrador = c.get("borrador") is True
        detalle = {"asunto": asunto, "mensaje": mensaje[:2000], "documento": resumen[0], "firmantes": firmantes, "copias": copias,
                   "ancla": str(c.get("ancla") or ""), "borrador": borrador}
        para = ", ".join(f["correo"] for f in firmantes)
        try:
            sobre = c_tr.docusign_sobre(asunto, mensaje, archivos[0], firmantes, copias, str(c.get("ancla") or ""), borrador)
        except ErrorDeNegocio as e:
            self._retener_vetado(n, "docusign", "enviar a firmar", para, detalle, e)
            raise
        self._escribe(n, "docusign", "crear borrador de sobre" if borrador else "enviar a firmar", f"docusign:{para}", detalle, cred,
                      lambda: c_tr.docusign_enviar(v, sobre, t), ("sobre", "estado"), firmantes=firmantes)
        self.mensajes.append({"nodo": n["id"], "operacion": "docusign_sobre", "para": para, "asunto": asunto, "cuerpo": mensaje[:2000], "adjuntos": resumen,
                              "enviado": not self.vars[self._sal(n)]["bloqueado"] and not borrador, "bloqueado": self.vars[self._sal(n)]["bloqueado"]})
        return "ok"


# =====================================================================
#   probar una conexión (sin escribir nada)
# =====================================================================

def probar_conexion(tipo: str, v: dict, cred: dict) -> Optional[dict]:
    """{ok, detalle} para los tipos de esta fase (solo lecturas: perfil, cuenta o token), o None si no es uno de ellos."""
    fn = {"jira": c_tr.jira_probar, "trello": c_tr.trello_probar, "asana": c_tr.asana_probar, "notion": c_tr.notion_probar, "hubspot": c_tr.hubspot_probar,
          "salesforce": c_tr.sf_probar, "zoho": c_tr.zoho_probar, "docusign": c_tr.docusign_probar}.get(tipo)
    if fn is None:
        return None
    return {"ok": True, "detalle": fn(v)}
