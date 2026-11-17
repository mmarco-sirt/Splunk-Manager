"""
Cobertura de índices por los casos de uso: qué índices reciben datos y no los consulta ningún caso de uso «CU-*»
habilitado (fuentes que se ingestan pero para las que no hay casos de uso).

Qué índices consulta una alerta se averigua sin ejecutarla, a partir de su SPL:
  1. Se sustituyen sus macros (las que ve desde su app y su propietario), también las anidadas.
  2. Se buscan los filtros index=, sourcetype=, eventtype= y tag= (también «campo IN (…)» e index::x). Los negados
     (index!=x, NOT index=x) y los de comandos que escriben (| collect index=x…) no cuentan.
  3. eventtype=X -> la búsqueda del eventtype, con sus macros (y así sucesivamente).
     tag=X       -> los eventtypes y sourcetypes que tienen ese tag en tags.conf.
     data models -> (tstats … from datamodel=X.Y, | datamodel X Y, | from datamodel:X.Y, | pivot X Y) las restricciones
                    del dataset y de sus padres, p. ej. «(`cim_Authentication_indexes`) tag=authentication».
  4. Si la alerta nombra índices concretos, cubre los índices que encajan (admiten comodines: index=win*).
     Si solo restringe por sourcetype (o con index=*), cubre los índices en los que se han visto esos sourcetypes en el
     periodo analizado.
     index=* sin sourcetype, o sin índice ni sourcetype (busca en los índices por defecto de su rol), no marca ningún
     índice como cubierto: se señala aparte, porque en realidad podría cubrir cualquiera.

Solo cuentan como cobertura los casos de uso de producción habilitados. Si un índice solo lo consultan casos de uso
deshabilitados o versiones de pruebas («TEST - CU-*») se indica, pero sigue sin estar cubierto.

La lista de índices ignorados (general y por cliente) se guarda en indices_ignorados.json, junto a config.py, para que
la usen igual la web y el CLI.
"""
import fnmatch
import json
import os
import re
import tempfile
import threading

from core.conventions import is_cu_alert, is_test_alert
from core.dependencies import resolve
from core.paths import BASE_DIR
from core.spl import expand_macros, macro_calls, strip_comments

DATA_DAYS = 7
MAX_DEPTH = 10

# Índices internos y de sistema (de Splunk, Enterprise Security y otras apps) que no tiene sentido cubrir con un CU
SYSTEM_INDEXES = (
    "_*", "summary", "history", "splunklogger", "lastchanceindex", "cim_modactions", "notable", "notable_summary",
    "risk", "threat_activity", "sequenced_events", "endpoint_summary", "gia_summary", "ioc", "whois", "ba_test",
    "audit_summary", "cim_summary", "mint", "splunk_summary", "ueba", "ubaroute",
)

# Comandos que generan resultados sin buscar eventos en índices
NO_EVENT_COMMANDS = {
    "inputlookup", "rest", "makeresults", "dbxquery", "ldapsearch", "ldapfetch", "metadata", "mstats", "mcatalog",
    "inputcsv", "loadjob", "savedsearch", "gentimes", "dbinspect", "eventcount", "datamodelsimple",
}
# Comandos que escriben: su «index=» es el destino, no lo que se busca
_WRITE_SEGMENT = re.compile(
    r"\|\s*(?:collect|summaryindex|sistats|mcollect|meventcollect|tscollect|sendalert|sendemail|outputlookup|outputcsv)\b"
    r"[^|\]]*", re.IGNORECASE)
_VALUE = r"(\"(?:[^\"\\]|\\.)*\"|'[^']*'|[^\s()|\[\],]+)"
_FIELD_VALUE = re.compile(r"(?<![\w.:$])(index|sourcetype|eventtype|tag)\s*(!=|==|=)\s*" + _VALUE, re.IGNORECASE)
_FIELD_IN = re.compile(r"(?<![\w.:$])(index|sourcetype|eventtype|tag)\s+IN\s*\(([^)]*)\)", re.IGNORECASE)
_FIELD_COLON = re.compile(r"(?<![\w.:$])(index|sourcetype)::([^\s()|\[\],]+)", re.IGNORECASE)
_NEGATED = re.compile(r"\bNOT\s*\(?\s*$", re.IGNORECASE)
_DM_ASSIGN = re.compile(r"\bdatamodel\s*[=:]\s*\"?([\w-]+)(?:\.([\w.-]+))?\"?", re.IGNORECASE)
_DM_COMMAND = re.compile(r"\|\s*(?:datamodel|pivot)\s+\"?([\w-]+)\"?(?:\s+\"?([\w-]+)\"?)?", re.IGNORECASE)
_DM_MODES = {"search", "flat", "acceleration_search", "acceleration_search_string"}
_FIRST_COMMAND = re.compile(r"^\s*\|\s*([A-Za-z_][\w-]*)")
# Dos filtros unidos por OR («eventtype=a OR sourcetype=b») y listas de eventtypes o tags («tag IN (a, b)»)
_TERM = r"(index|sourcetype|eventtype|tag)\s*(?:==|=|::)\s*(?:\"[^\"]*\"|[^\s()|]+)"
_FILTER_OR = re.compile(_TERM + r"[\s)]*\bOR\b[\s(]*(index|sourcetype|eventtype|tag)\s*(?:==|=|::)", re.IGNORECASE)
_LIST_IN = re.compile(r"(?<![\w.:$])(?:eventtype|tag)\s+IN\s*\(", re.IGNORECASE)

STATUS_LABELS = {
    "used": "Cubierto por algún CU",
    "inactive_only": "Solo lo usan CU deshabilitados o de pruebas",
    "unused": "Sin ningún CU",
    "ignored": "Ignorado",
}
MODE_LABELS = {
    "index": "Índices concretos",
    "sourcetype": "Por sourcetype",
    "all": "index=* (cualquier índice)",
    "default": "Sin índice: índices por defecto del rol",
    "no_events": "No busca eventos (lookup, rest…)",
}
BROAD_MODES = ("all", "default")


def _clean(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value.strip()


def _unresolvable(value):
    return not value or "$" in value or "`" in value


def extract_filters(text):
    """
    Filtros de un SPL (ya sin macros): {index, sourcetype, eventtype, tag: [valores], datamodel: [(modelo, dataset)],
    unresolved: [texto]}. Los valores de índice y sourcetype van en minúsculas (Splunk no distingue mayúsculas).
    """
    text = _WRITE_SEGMENT.sub(" ", strip_comments(text))
    found = {"index": [], "sourcetype": [], "eventtype": [], "tag": [], "datamodel": [], "unresolved": []}

    def add(field, value):
        field = field.lower()
        value = _clean(value)
        if value.startswith("="):            # where index=="x"
            value = _clean(value[1:])
        if _unresolvable(value):
            found["unresolved"].append(f"{field}={value}")
            return
        if field in ("index", "sourcetype"):
            value = value.lower()
        if value not in found[field]:
            found[field].append(value)

    for match in _FIELD_VALUE.finditer(text):
        if match.group(2) == "!=" or _NEGATED.search(text[max(0, match.start() - 12):match.start()]):
            continue
        add(match.group(1), match.group(3))
    for match in _FIELD_IN.finditer(text):
        if _NEGATED.search(text[max(0, match.start() - 12):match.start()]):
            continue
        for value in match.group(2).split(","):
            if value.strip():
                add(match.group(1), value)
    for match in _FIELD_COLON.finditer(text):
        add(match.group(1), match.group(2))
    for pattern in (_DM_ASSIGN, _DM_COMMAND):
        for match in pattern.finditer(text):
            dataset = match.group(2) if match.group(2) and match.group(2).lower() not in _DM_MODES else None
            ref = (match.group(1), dataset)
            if ref not in found["datamodel"]:
                found["datamodel"].append(ref)
    return found


def first_command(spl):
    """Comando con el que empieza una búsqueda que empieza por «|» (en minúsculas), o None."""
    match = _FIRST_COMMAND.match(strip_comments(spl))
    return match.group(1).lower() if match else None


def matches(name, patterns):
    """True si 'name' encaja en alguno de los patrones (comodines * y ?, sin distinguir mayúsculas)."""
    name = str(name).lower()
    return any(fnmatch.fnmatchcase(name, str(p).lower()) for p in patterns)


def parse_datamodel(raw):
    """{dataset: {parent, constraints: [búsqueda]}} de la definición JSON de un data model ('' o inválida -> {})."""
    try:
        data = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except ValueError:
        return {}
    objects = {}
    for obj in data.get("objects") or []:
        name = obj.get("objectName")
        if not name:
            continue
        constraints = [c.get("search") for c in obj.get("constraints") or [] if c.get("search")]
        objects[name] = {"parent": obj.get("parentName"), "constraints": constraints}
    return objects


class Knowledge:
    """
    Objetos de conocimiento del cliente para resolver búsquedas:
      macros:     [{name (stanza), app, owner, sharing, definition, args, iseval, disabled}]
      eventtypes: [{name, app, owner, sharing, search, disabled}]
      tags:       [{name: 'eventtype=x' | 'sourcetype=y' | …, tags: [tags habilitados]}]
      datamodels: {modelo: {dataset: {parent, constraints}}}
    """

    def __init__(self, macros=(), eventtypes=(), tags=(), datamodels=None):
        self.macros = {}
        for m in macros:
            if not m.get("disabled"):
                self.macros.setdefault(m.get("name"), []).append(m)
        self.eventtypes = [e for e in eventtypes if not e.get("disabled")]
        self.tagged = {}
        for stanza in tags:
            field, sep, value = str(stanza.get("name") or "").partition("=")
            field, value = field.strip().lower(), _clean(value)
            if not sep or field not in ("eventtype", "sourcetype") or not value:
                continue
            for tag in stanza.get("tags") or []:
                entry = self.tagged.setdefault(str(tag).lower(), {"eventtype": [], "sourcetype": []})
                entry[field].append(value.lower() if field == "sourcetype" else value)
        self.datamodels = datamodels or {}
        self._models_lower = {k.lower(): k for k in self.datamodels}

    def macro_view(self, context):
        """Objeto con get(stanza) que devuelve la macro que usaría Splunk desde 'context' ({app, owner})."""
        knowledge = self

        class _View:
            def get(self, stanza):
                return resolve(knowledge.macros.get(stanza, []), context)[0]
        return _View()

    def find_eventtypes(self, pattern, context):
        """Eventtypes visibles desde 'context' cuyo nombre encaja con 'pattern' (admite comodines)."""
        by_name = {}
        for e in self.eventtypes:
            if fnmatch.fnmatchcase(e["name"].lower(), pattern.lower()):
                by_name.setdefault(e["name"], []).append(e)
        return [chosen for chosen in (resolve(c, context)[0] for c in by_name.values()) if chosen]

    def find_tagged(self, pattern):
        """(eventtypes, sourcetypes) que tienen un tag que encaja con 'pattern'."""
        eventtypes, sourcetypes = [], []
        for tag, entry in self.tagged.items():
            if fnmatch.fnmatchcase(tag, pattern.lower()):
                eventtypes += [e for e in entry["eventtype"] if e not in eventtypes]
                sourcetypes += [s for s in entry["sourcetype"] if s not in sourcetypes]
        return eventtypes, sourcetypes

    def datamodel_constraints(self, model, dataset=None):
        """
        Restricciones del dataset y de sus padres dentro del modelo (sin dataset, las de los datasets raíz).
        None si el modelo (o el dataset) no existe.
        """
        key = self._models_lower.get(str(model).lower())
        if key is None:
            return None
        objects = self.datamodels[key]
        if not dataset:
            roots = [o for o in objects.values() if o["parent"] not in objects]
            return [c for o in roots for c in o["constraints"]]
        # 'Authentication.Failed_Authentication' o solo 'Failed_Authentication'
        name = next((part for part in reversed(dataset.split(".")) if part in objects), None)
        if name is None:
            return None
        constraints, seen = [], set()
        while name in objects and name not in seen:
            seen.add(name)
            constraints = objects[name]["constraints"] + constraints
            name = objects[name]["parent"]
        return constraints


def _chain(via, step):
    return step if not via else f"{via} → {step}"


def _node(via):
    return {"via": via, "index": [], "sourcetype": [], "groups": [], "any_of": False}


def _combined_with_or(text):
    """True si el SPL une con OR filtros que no son del mismo índice/sourcetype, o lista eventtypes o tags con IN."""
    text = strip_comments(text)
    if _LIST_IN.search(text):
        return True
    for match in _FILTER_OR.finditer(text):
        first, second = match.group(1).lower(), match.group(2).lower()
        if first != second or first in ("eventtype", "tag"):
            return True
    return False


def resolve_search(spl, context, knowledge):
    """
    Árbol de lo que consulta un SPL visto desde 'context' ({app, owner}). Cada nodo es un texto SPL (la búsqueda, un
    eventtype, las restricciones de un data model…) con sus filtros: {via, index: [], sourcetype: [], groups: [[nodo]],
    any_of}. Cada grupo es un filtro eventtype=, tag= o datamodel= (sus nodos son las alternativas que encajan).
    En un mismo nodo los filtros se cumplen a la vez (Y), salvo que el SPL los una con OR (any_of = True).
    'via' explica de dónde sale (None = escrito en la propia búsqueda o en sus macros).
    Devuelve {tree, index: {valor: vía}, sourcetype: {valor: vía}, issues: [texto], first_command}.
    """
    flat = {"index": {}, "sourcetype": {}}
    issues = []
    nodes = {}
    view = knowledge.macro_view(context)

    def issue(text):
        if text not in issues:
            issues.append(text)

    def child(key, via, text, depth):
        """Nodo de un eventtype o data model (el mismo objeto si ya se visitó: el árbol puede repetir ramas)."""
        if key not in nodes:
            nodes[key] = _node(via)
            fill(nodes[key], text, depth)
        return nodes[key]

    def fill(node, text, depth):
        expanded, _ = expand_macros(text, view)
        for call in macro_calls(expanded):
            issue(f"Macro sin resolver: {call['call']}")
        filters = extract_filters(expanded)
        via = node["via"]
        node["any_of"] = _combined_with_or(expanded)
        if node["any_of"]:
            issue("Combina filtros con OR: se cuentan los índices de todos ellos")
        for kind in ("index", "sourcetype"):
            node[kind] = list(filters[kind])
            for value in filters[kind]:
                flat[kind].setdefault(value, via)
        for raw in filters["unresolved"]:
            if "`" not in raw:
                issue(f"Filtro con un token que no se puede resolver: {raw}")
        if depth >= MAX_DEPTH:
            return
        for pattern in filters["eventtype"]:
            eventtypes = knowledge.find_eventtypes(pattern, context)
            if not eventtypes:
                issue(f"Eventtype no encontrado: {pattern}")
            group = [child(("eventtype", e["name"].lower()), _chain(via, f"eventtype {e['name']}"),
                           e.get("search") or "", depth + 1) for e in eventtypes]
            if group:
                node["groups"].append(group)
        for pattern in filters["tag"]:
            eventtypes, sourcetypes = knowledge.find_tagged(pattern)
            if not eventtypes and not sourcetypes:
                issue(f"Ningún eventtype ni sourcetype tiene el tag «{pattern}»")
            step = _chain(via, f"tag {pattern}")
            group = []
            if sourcetypes:
                group.append(dict(_node(step), sourcetype=sourcetypes))
                for sourcetype in sourcetypes:
                    flat["sourcetype"].setdefault(sourcetype, step)
            for name in eventtypes:
                for e in knowledge.find_eventtypes(name, context):
                    group.append(child(("eventtype", e["name"].lower()), _chain(step, f"eventtype {e['name']}"),
                                       e.get("search") or "", depth + 1))
            if group:
                node["groups"].append(group)
        for model, dataset in filters["datamodel"]:
            label = f"{model}.{dataset}" if dataset else model
            constraints = knowledge.datamodel_constraints(model, dataset)
            if constraints is None:
                issue(f"Data model no encontrado: {label}")
                continue
            # Las restricciones de un dataset y de sus padres se cumplen a la vez: van en un mismo texto
            text = " ".join(f"({c})" for c in constraints)
            node["groups"].append([child(("datamodel", label.lower()), _chain(via, f"data model {label}"), text, depth + 1)])

    tree = _node(None)
    fill(tree, spl or "", 0)
    return {"tree": tree, "index": flat["index"], "sourcetype": flat["sourcetype"], "issues": issues,
            "first_command": first_command(spl or "")}


def _specific(patterns):
    return [p for p in patterns if p.strip("*") != ""]


def _union(parts):
    result = {}
    for part in parts:
        for name, via in part.items():
            result.setdefault(name, via)
    return result


def _intersection(parts):
    """Índices presentes en todas las partes; la vía es la de la última que la explica (la más detallada)."""
    names = set(parts[0]).intersection(*parts[1:])
    result = {}
    for name in sorted(names):
        vias = [p[name] for p in parts if p[name]]
        result[name] = vias[-1] if vias else None
    return result


def _cover(node, index_names, sourcetype_indexes, sources, visiting):
    """
    Índices que cubre un nodo: {índice: vía}, o None si no lo limita (sin índice ni sourcetype concretos).
    Sus filtros (índices, sourcetypes y cada grupo eventtype/tag/data model) se cumplen a la vez, así que se cruzan;
    si el SPL los une con OR, se suman. Dentro de un grupo, las alternativas se suman.
    """
    if id(node) in visiting:
        return None
    visiting.add(id(node))
    via = node["via"]
    parts = []
    specific = _specific(node["index"])
    if specific:
        sources.add("index")
        parts.append({name: via for name in index_names if any(fnmatch.fnmatchcase(name, p) for p in specific)})
    elif node["index"]:
        sources.add("all")
    # sourcetype=* no filtra nada (p. ej. «tstats … from datamodel=X where sourcetype=*»)
    sourcetypes = _specific(node["sourcetype"])
    if sourcetypes:
        sources.add("sourcetype")
        part = {}
        for sourcetype, indexes in sorted(sourcetype_indexes.items()):
            if any(fnmatch.fnmatchcase(sourcetype, p) for p in sourcetypes):
                for name in indexes:
                    part.setdefault(name, _chain(via, f"sourcetype {sourcetype}"))
        parts.append(part)
    for group in node["groups"]:
        members = [_cover(sub, index_names, sourcetype_indexes, sources, visiting) for sub in group]
        if members and all(m is not None for m in members):    # una alternativa sin límite no acota nada
            parts.append(_union(members))
    visiting.discard(id(node))
    if not parts:
        return None
    return _union(parts) if node.get("any_of") else _intersection(parts)


def coverage(resolved, index_names, sourcetype_indexes):
    """
    Qué índices cubre una búsqueda resuelta: (modo, {índice: vía}).
      index_names: todos los índices conocidos (en minúsculas)
      sourcetype_indexes: {sourcetype (minúsculas): [índices en los que se ha visto en el periodo]}
    Modo: index (nombra índices), sourcetype (solo por sourcetype), all (index=* sin más), no_events (lookup, rest…)
    o default (sin índice ni sourcetype: los índices por defecto del rol).
    """
    sources = set()
    covered = _cover(resolved["tree"], index_names, sourcetype_indexes, sources, set())
    if covered is None:
        covered = {}
        sources.discard("index")
        sources.discard("sourcetype")
    if "index" in sources:
        return "index", covered
    if "sourcetype" in sources:
        return "sourcetype", covered
    if "all" in sources:
        return "all", covered
    if resolved["first_command"] in NO_EVENT_COMMANDS:
        return "no_events", covered
    return "default", covered


def _alert_kind(row):
    if row["disabled"]:
        return "disabled"
    return "test" if is_test_alert(row["name"]) else "active"


def analyze(saved_searches, indexes, data_rows, knowledge, ignored=(), system=SYSTEM_INDEXES):
    """
    saved_searches: [{name, app, owner, sharing, disabled, search}] (se usan solo las CU-* y TEST - CU-*)
    indexes: [{name, datatype, disabled}] (todos los índices del cliente)
    data_rows: [{index, sourcetype, count, last}] (eventos del periodo por índice y sourcetype)
    ignored: [{index (patrón), reason, scope}] índices que el usuario ha decidido no revisar
    """
    data = {}
    sourcetype_indexes = {}
    for row in data_rows:
        name = str(row.get("index") or "").lower()
        if not name:
            continue
        count = _int(row.get("count"))
        entry = data.setdefault(name, {"events": 0, "last": None, "sourcetypes": {}})
        entry["events"] += count
        last = _float(row.get("last"))
        if last is not None and (entry["last"] is None or last > entry["last"]):
            entry["last"] = last
        sourcetype = str(row.get("sourcetype") or "").lower()
        if sourcetype:
            entry["sourcetypes"][sourcetype] = entry["sourcetypes"].get(sourcetype, 0) + count
            sourcetype_indexes.setdefault(sourcetype, [])
            if name not in sourcetype_indexes[sourcetype]:
                sourcetype_indexes[sourcetype].append(name)

    info = {str(i["name"]).lower(): i for i in indexes}
    names = sorted(set(info) | set(data))

    alerts = []
    covered_by = {}
    for s in saved_searches:
        if not is_cu_alert(s["name"]):
            continue
        resolved = resolve_search(s.get("search") or "", {"app": s.get("app"), "owner": s.get("owner")}, knowledge)
        mode, covered = coverage(resolved, names, sourcetype_indexes)
        kind = _alert_kind(s)
        alert = {
            "name": s["name"], "app": s.get("app"), "owner": s.get("owner"), "sharing": s.get("sharing"),
            "disabled": bool(s.get("disabled")), "kind": kind, "mode": mode,
            "index_filters": sorted(resolved["index"]), "sourcetype_filters": sorted(resolved["sourcetype"]),
            "indexes": sorted(covered), "issues": resolved["issues"],
        }
        alerts.append(alert)
        for name, via in covered.items():
            covered_by.setdefault(name, []).append({"name": s["name"], "app": s.get("app"), "kind": kind, "via": via})
    alerts.sort(key=lambda a: a["name"].lower())

    ignored = [i for i in ignored or [] if i.get("index")]
    rows, excluded, without_data, metrics = [], [], [], []
    for name in names:
        meta = info.get(name) or {}
        if matches(name, system):
            if name in data:
                excluded.append(name)
            continue
        if str(meta.get("datatype") or "event") == "metric":
            metrics.append(name)
            continue
        if name not in data or not data[name]["events"]:
            without_data.append(name)
            continue
        users = sorted(covered_by.get(name, []), key=lambda u: (u["kind"] != "active", u["name"].lower()))
        active = [u for u in users if u["kind"] == "active"]
        rule = next((i for i in ignored if matches(name, [i["index"]])), None)
        if active:
            status = "used"
        elif rule:
            status = "ignored"
        else:
            status = "inactive_only" if users else "unused"
        entry = data[name]
        rows.append({
            "name": meta.get("name") or name, "events": entry["events"], "last": entry["last"],
            "sourcetypes": [{"name": st, "events": n} for st, n in sorted(entry["sourcetypes"].items(), key=lambda x: -x[1])],
            "status": status, "used_by": users, "active_count": len(active),
            "ignored": {"index": rule["index"], "reason": rule.get("reason") or "", "scope": rule.get("scope")} if rule else None,
            "disabled": bool(meta.get("disabled")),
        })
    rows.sort(key=lambda r: (["unused", "inactive_only", "ignored", "used"].index(r["status"]), -r["events"]))

    active_alerts = [a for a in alerts if a["kind"] == "active"]
    counts = {status: sum(1 for r in rows if r["status"] == status) for status in STATUS_LABELS}
    return {
        "period_days": DATA_DAYS,
        "summary": {
            "indexes": len(names), "with_data": len(rows), **counts,
            "excluded": len(excluded), "without_data": len(without_data), "metrics": len(metrics),
            "alerts": len(alerts), "active_alerts": len(active_alerts),
            "broad_alerts": sum(1 for a in active_alerts if a["mode"] in BROAD_MODES),
            "issues_alerts": sum(1 for a in active_alerts if a["issues"]),
        },
        "indexes": rows,
        "alerts": alerts,
        "excluded": excluded, "without_data": without_data, "metrics": metrics,
        "system_patterns": list(system),
    }


def _int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# --- Índices ignorados (indices_ignorados.json) -------------------------------------------------

IGNORED_FILE = "indices_ignorados.json"
GLOBAL_SCOPE = "*"
_lock = threading.Lock()


def ignored_path():
    return os.path.join(BASE_DIR, IGNORED_FILE)


def _read_ignored():
    try:
        with open(ignored_path(), encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except ValueError as e:
        raise ValueError(f"{IGNORED_FILE} no es un JSON válido: {e}")
    return data if isinstance(data, dict) else {}


def _normalize(entries):
    result, seen = [], set()
    for e in entries or []:
        if isinstance(e, str):
            e = {"index": e}
        pattern = str(e.get("index") or "").strip()
        if not pattern or pattern.lower() in seen:
            continue
        if re.search(r"[\s\"'`|=]", pattern):
            raise ValueError(f"Patrón de índice no válido: «{pattern}»")
        seen.add(pattern.lower())
        result.append({"index": pattern, "reason": str(e.get("reason") or "").strip()})
    return result


def load_ignored(client=None):
    """{'global': [{index, reason}], 'client': [{index, reason}]} (el del cliente solo si se indica)."""
    data = _read_ignored()
    return {"global": _normalize(data.get(GLOBAL_SCOPE)), "client": _normalize(data.get(client)) if client else []}


def ignored_rules(client):
    """Reglas de un cliente para analyze(): primero las suyas y luego las generales."""
    lists = load_ignored(client)
    return ([{**e, "scope": "client"} for e in lists["client"]] + [{**e, "scope": "global"} for e in lists["global"]])


def save_ignored(scope, entries):
    """Guarda la lista de un cliente (scope = nombre) o la general (scope = '*'). Devuelve la lista guardada."""
    if not scope:
        raise ValueError("Falta el cliente.")
    entries = _normalize(entries)
    with _lock:
        data = _read_ignored()
        if entries:
            data[scope] = entries
        else:
            data.pop(scope, None)
        folder = os.path.dirname(ignored_path())
        fd, tmp = tempfile.mkstemp(prefix=".indices_ignorados_", suffix=".json", dir=folder)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, ignored_path())
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
    return entries


def rename_client(old, new):
    """Mantiene la lista de ignorados de un cliente al renombrarlo."""
    if not old or old == new:
        return
    with _lock:
        data = _read_ignored()
    if old in data:
        entries = data[old]
        save_ignored(new, entries)
        save_ignored(old, [])
