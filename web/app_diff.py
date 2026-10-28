"""
Cambios entre dos versiones de una app (dos paquetes subidos) o entre un paquete y lo que hay en un cliente: objetos
añadidos, eliminados y modificados (saved searches, macros, eventtypes, lookups, dashboards…), campo a campo.

- Entre paquetes se distingue default/ y local/: los campos de local/ llevan « [local]».
- Contra un cliente se compara la configuración efectiva del paquete (default + local) con la que devuelve Splunk para
  esa app, solo en los campos que define el paquete (el resto son valores por defecto de Splunk). En el cliente no se
  puede saber qué viene de su default/ y qué de su local/ (p. ej. una alerta editada desde Splunk Web).

De las saved searches se indica además si cambia su SPL ('spl_changed') y qué macros cambiadas del paquete usa
('affected_by'), para elegir qué búsquedas conviene volver a ejecutar. Las que no cambian pero usan una macro que sí
cambia tienen el estado 'affected': su resultado puede ser distinto aunque su configuración sea la misma.
"""
import hashlib
import re
from concurrent.futures import ThreadPoolExecutor

from splunklib.binding import HTTPError

from core.spl import macro_calls
from core.splunk_conf import effective_stanza, merge_layers, normalize_value
from web.explorer import _read_json
from web.operations import diff_fields

CONF_LABELS = {
    "savedsearches": "Saved searches", "macros": "Macros", "eventtypes": "Eventtypes", "tags": "Tags",
    "transforms": "Transforms (lookups y extracciones)", "props": "Props", "collections": "Colecciones del KV Store",
    "datamodels": "Data models", "workflow_actions": "Workflow actions", "alert_actions": "Alert actions",
    "app": "app.conf", "metadata": "Permisos (metadata)",
}
FILE_LABELS = {"lookups": "Lookups (archivos)", "views": "Dashboards y navegación", "files": "Otros archivos"}
TYPE_ORDER = ["savedsearches", "macros", "eventtypes", "tags", "transforms", "props", "collections", "lookups", "views",
              "datamodels", "app", "metadata"]
STATUSES = ("added", "removed", "modified", "affected", "same", "present")
VIEW_PATH = re.compile(r"^(default|local)/data/ui/(views|nav)/")
TEXT_FILE = re.compile(r"\.(xml|csv|txt|md|json|js|css|html?|py|conf|spec)$", re.IGNORECASE)


def type_label(kind):
    return CONF_LABELS.get(kind) or FILE_LABELS.get(kind) or f"{kind}.conf"


def _type_key(kind):
    return (TYPE_ORDER.index(kind) if kind in TYPE_ORDER else len(TYPE_ORDER), kind)


def _norm(value):
    """Valor para comparar paquete y cliente: booleanos como 1/0, números como texto y sin espacios ni «\\» de sobra."""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, list):
        return "\n".join(str(v) for v in value)
    text = normalize_value(value)
    return {"true": "1", "false": "0"}.get(text.lower(), text)


# --- Objetos de un paquete -----------------------------------------------------------------

def conf_objects(package):
    """{tipo: {objeto: {"default": {clave: valor}, "local": {...}}}} de los .conf y de los permisos (metadata)."""
    result = {}
    for name in package.conf_names():
        objects = {}
        for layer in ("default", "local"):
            for stanza, fields in package.stanzas(name, layer).items():
                objects.setdefault(stanza, {})[layer] = fields
        result[name] = objects
    meta = {}
    for layer in ("default", "local"):
        conf = package.conf(layer, None)
        for stanza, fields in (conf.stanzas if conf else {}).items():
            meta.setdefault(stanza, {})[layer] = fields
    if meta:
        result["metadata"] = meta
    return result


def file_objects(package):
    """{tipo: {ruta: {sha256, size, path}}} de los lookups, dashboards/navegación y el resto de archivos."""
    result = {"lookups": {}, "views": {}, "files": {}}
    for path, data in package.files.items():
        if path.endswith(".conf") and path.startswith(("default/", "local/")) and path.count("/") == 1:
            continue
        if path in ("metadata/default.meta", "metadata/local.meta"):
            continue
        kind = "lookups" if path.startswith("lookups/") else "views" if VIEW_PATH.match(path) else "files"
        name = path[len("lookups/"):] if kind == "lookups" else path
        result[kind][name] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "path": path}
    return {k: v for k, v in result.items() if v}


def _flat(obj):
    """Campos de un objeto por capas: los de local/ con « [local]»."""
    flat = {key: _norm(value) for key, value in (obj.get("default") or {}).items()}
    flat.update({f"{key} [local]": _norm(value) for key, value in (obj.get("local") or {}).items()})
    return flat


def macro_closure(spl, macros):
    """Macros del paquete que usa un SPL, también a través de otras macros ({stanza: campos} efectivos)."""
    used, pending = set(), [spl]
    while pending:
        for call in macro_calls(pending.pop()):
            if call["stanza"] in macros and call["stanza"] not in used:
                used.add(call["stanza"])
                pending.append(macros[call["stanza"]].get("definition") or "")
    return used


def _file_diff(old_pkg, new_pkg, old, new):
    if old is None or new is None or old["sha256"] == new["sha256"]:
        return []
    if TEXT_FILE.search(new["path"]):
        before = old_pkg.text(old["path"])[0] if old_pkg else ""
        after = new_pkg.text(new["path"])[0]
        return diff_fields({"contenido": before}, {"contenido": after})
    return [{"field": "tamaño", "before": str(old["size"]), "after": str(new["size"]), "diff": None}]


def _summarize(items, kinds, errors=None):
    types = []
    for kind in sorted(kinds, key=_type_key):
        counts = {s: 0 for s in STATUSES}
        for item in items:
            if item["type"] == kind:
                counts[item["status"]] += 1
        types.append({"type": kind, "label": type_label(kind), "counts": counts, "error": (errors or {}).get(kind)})
    return types


def _mark_searches(items, package):
    """
    Añade a las saved searches si cambia su SPL y qué macros cambiadas del paquete usan; las que no cambian pero usan una
    macro que cambia pasan a 'affected'.
    """
    changed_macros = {i["name"] for i in items if i["type"] == "macros" and i["status"] in ("added", "modified", "removed")}
    macros = merge_layers(package.stanzas("macros", "default"), package.stanzas("macros", "local"))
    searches = package.stanzas("savedsearches")
    for item in items:
        if item["type"] != "savedsearches":
            continue
        fields = {f["field"].replace(" [local]", "") for f in item.get("fields") or []}
        item["spl_changed"] = item["status"] == "added" or "search" in fields
        spl = effective_stanza(searches, item["name"]).get("search", "") if item["name"] in searches else ""
        item["affected_by"] = sorted(macro_closure(spl, macros) & changed_macros)
        if item["status"] == "same" and item["affected_by"]:
            item["status"] = "affected"


# --- Paquete frente a paquete -------------------------------------------------------------------

def diff_packages(new, old):
    """Cambios de la versión 'new' respecto a 'old' (dos Package)."""
    items = []
    kinds = set()
    new_confs, old_confs = conf_objects(new), conf_objects(old)
    for kind in set(new_confs) | set(old_confs):
        before_all, after_all = old_confs.get(kind, {}), new_confs.get(kind, {})
        kinds.add(kind)
        for name in sorted(set(before_all) | set(after_all), key=str.lower):
            before, after = before_all.get(name), after_all.get(name)
            fields = diff_fields(_flat(before or {}), _flat(after or {}))
            status = "added" if before is None else "removed" if after is None else "modified" if fields else "same"
            items.append({"type": kind, "name": name, "status": status, "fields": fields,
                          "layers": sorted((after or before or {}).keys())})
    new_files, old_files = file_objects(new), file_objects(old)
    for kind in set(new_files) | set(old_files):
        kinds.add(kind)
        before_all, after_all = old_files.get(kind, {}), new_files.get(kind, {})
        for name in sorted(set(before_all) | set(after_all), key=str.lower):
            before, after = before_all.get(name), after_all.get(name)
            if before and after and before["sha256"] == after["sha256"]:
                status, fields = "same", []
            else:
                status = "added" if before is None else "removed" if after is None else "modified"
                fields = _file_diff(old, new, before, after)
            items.append({"type": kind, "name": name, "status": status, "fields": fields})
    _mark_searches(items, new)
    return {"types": _summarize(items, kinds), "items": [i for i in items if i["status"] != "same"]}


# --- Paquete frente a un cliente ------------------------------------------------------------------

def _app_entries(service, path, app, **query):
    data = _read_json(service, path, count=0, owner="nobody", app=app, **query)
    return [e for e in data.get("entry", []) if (e.get("acl") or {}).get("app") == app]


def _http_error(error):
    if error.status in (401, 403):
        return f"Sin permiso para leerlo en el cliente (HTTP {error.status})."
    return f"Splunk respondió HTTP {error.status}."


def installed_app(service, app):
    """Versión y estado de la app instalada en el cliente, o None si no está instalada."""
    try:
        entry = (_read_json(service, f"/services/apps/local/{app}").get("entry") or [None])[0]
    except HTTPError as e:
        if e.status == 404:
            return None
        raise
    if not entry:
        return None
    c = entry.get("content") or {}
    return {"version": c.get("version") or "", "label": c.get("label") or app, "disabled": _norm(c.get("disabled")) == "1",
            "build": str(c.get("build") or "")}


def client_snapshot(service, app, conf_names):
    """
    Lo que tiene el cliente de una app: {installed, confs: {nombre: {stanza: campos}}, lookups: set, views: {nombre: xml},
    errors: {tipo: motivo}}. Si la app no está instalada no se lee nada más.
    """
    snapshot = {"installed": installed_app(service, app), "confs": {}, "lookups": set(), "views": {}, "errors": {}}
    if not snapshot["installed"]:
        return snapshot

    def read(job):
        kind, path, query = job
        try:
            return kind, _app_entries(service, path, app, **query), None
        except HTTPError as e:
            return kind, None, _http_error(e)

    jobs = [(name, f"configs/conf-{name}", {}) for name in conf_names if name != "app"]
    jobs += [("lookups", "data/lookup-table-files", {}), ("views", "data/ui/views", {"f": ["eai:data"]})]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for kind, entries, error in pool.map(read, jobs):
            if error:
                snapshot["errors"][kind] = error
            elif kind == "lookups":
                snapshot["lookups"] = {e.get("name") for e in entries}
            elif kind == "views":
                snapshot["views"] = {e.get("name"): (e.get("content") or {}).get("eai:data") or "" for e in entries}
            else:
                snapshot["confs"][kind] = {
                    e.get("name"): {k: v for k, v in (e.get("content") or {}).items() if not k.startswith("eai:")}
                    for e in entries}
    return snapshot


def _xml(text):
    return "\n".join(line.strip() for line in str(text or "").replace("\r\n", "\n").split("\n") if line.strip())


def diff_client(package, snapshot):
    """
    Cambios del paquete respecto a lo que hay en el cliente (snapshot de client_snapshot). Si la app no está instalada,
    todo es nuevo.
    """
    items, kinds = [], set()
    errors = dict(snapshot.get("errors") or {})
    for name in package.conf_names():
        if name == "app":
            continue
        kinds.add(name)
        if name in errors:
            continue
        default, local = package.stanzas(name, "default"), package.stanzas(name, "local")
        effective = merge_layers(default, local)
        client = snapshot["confs"].get(name, {})
        for stanza in sorted(set(effective) | set(client), key=str.lower):
            if stanza == "default":
                continue
            if stanza not in effective:
                items.append({"type": name, "name": stanza, "status": "removed", "fields": []})
                continue
            fields_pkg = effective_stanza(effective, stanza)
            if stanza not in client:
                items.append({"type": name, "name": stanza, "status": "added", "layers": [],
                              "fields": diff_fields({}, {k: _norm(v) for k, v in fields_pkg.items()})})
                continue
            in_local = set(local.get(stanza, {}))
            label = lambda key: f"{key} [local]" if key in in_local else key
            before = {label(k): _norm(client[stanza].get(k)) for k in fields_pkg if k in client[stanza]}
            after = {label(k): _norm(v) for k, v in fields_pkg.items()}
            fields = diff_fields(before, after)
            items.append({"type": name, "name": stanza, "status": "modified" if fields else "same", "fields": fields})

    files = file_objects(package)
    client_lookups, client_views = snapshot.get("lookups") or set(), snapshot.get("views") or {}
    if "lookups" in files or client_lookups:
        kinds.add("lookups")
        if "lookups" not in errors:
            for name in sorted(set(files.get("lookups", {})) | client_lookups, key=str.lower):
                status = ("added" if name not in client_lookups else "removed" if name not in files.get("lookups", {})
                          else "present")   # en los dos: Splunk no deja comparar el contenido por REST
                items.append({"type": "lookups", "name": name, "status": status, "fields": []})
    views = {p.rsplit("/", 1)[1][:-4]: info for p, info in files.get("views", {}).items() if "/views/" in p and p.endswith(".xml")}
    if views or client_views:
        kinds.add("views")
        if "views" not in errors:
            for name in sorted(set(views) | set(client_views), key=str.lower):
                if name not in client_views:
                    items.append({"type": "views", "name": name, "status": "added", "fields": []})
                elif name not in views:
                    items.append({"type": "views", "name": name, "status": "removed", "fields": []})
                else:
                    before, after = _xml(client_views[name]), _xml(package.text(views[name]["path"])[0])
                    fields = diff_fields({"contenido": before}, {"contenido": after}) if before != after else []
                    items.append({"type": "views", "name": name, "status": "modified" if fields else "same", "fields": fields})
    _mark_searches(items, package)
    return {"types": _summarize(items, kinds, errors), "items": [i for i in items if i["status"] != "same"]}
