"""
Comparación de la configuración de un cliente de referencia con otros clientes, y copia de objetos entre ellos.

Para cada tipo de objeto se obtiene, por cliente, {clave: {"entry", "compare", "summary"}}:
  - entry: el elemento en el mismo formato que los backups (para poder copiarlo con las funciones de restore)
  - compare: campos planos que se comparan
  - summary: datos breves para mostrar en la tabla
"""
import json
import os
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor

from core.backup_files import VOLATILE_FIELDS, write_backup
from core.conventions import is_cu_alert, is_test_alert, strip_test_prefix
from core.knowledge import acl_of, all_macros, all_saved_searches
from core.paths import backup_dir, host_tag
from options.alerts.backup_alerts import _is_alert, _is_es_use_case
from options.alerts.restore_alerts import READ_ONLY_FIELDS
from options.dashboards.backup_dashboard import backup_dashboard
from options.macros.backup_macros import macro_entry
from web import services
from web.explorer import _read_json, list_apps, list_indexes, list_users
from web.operations import BACKUP_FOLDERS, SHARING_LEVELS, OperationError, diff_fields

FETCH_WORKERS = 6
COPY_LABEL = "antes_de_copiar"

# Campos principales de una saved search (modo de comparación «principales»)
MAIN_SAVED_SEARCH_FIELDS = {
    "search", "description", "disabled", "is_scheduled", "cron_schedule", "dispatch.earliest_time",
    "dispatch.latest_time", "alert_type", "alert_comparator", "alert_threshold", "alert_condition", "alert.severity",
    "alert.suppress", "alert.suppress.period", "alert.suppress.fields", "alert.digest_mode", "alert.expires",
    "actions", "action.email.to", "action.correlationsearch.label", "schedule_window",
}
IGNORED_SAVED_SEARCH_FIELDS = READ_ONLY_FIELDS | VOLATILE_FIELDS


def _text(value):
    """Valor normalizado para comparar: texto, listas ordenadas una por línea y booleanos como 0/1."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, list):
        return "\n".join(sorted(str(v) for v in value))
    text = str(value)
    lowered = text.strip().lower()
    if lowered in ("true", "false"):
        return "1" if lowered == "true" else "0"
    return text


def _flag(value):
    return _text(value) in ("1", "yes")


def _unique_key(items, name, app):
    return name if name not in items else f"{name} [{app}]"


# --- Lectura de cada tipo de objeto ---------------------------------------------

def _object_key(items, obj):
    """El nombre; si se repite, con su app. Las privadas llevan su propietario («CU-X [privada de juan]»)."""
    access = obj.access
    name = obj.name if access.get("sharing") != "user" else f"{obj.name} [privada de {access.get('owner')}]"
    return _unique_key(items, name, access.get("app"))


def _saved_searches(service, alerts):
    items = {}
    for s in all_saved_searches(service):
        is_alert = _is_alert(s) or _is_es_use_case(s)
        if is_alert != alerts:
            continue
        c = s.content
        acl = acl_of(s)
        entry = {"name": s.name, "content": dict(c), "acl": acl}
        if _is_es_use_case(s):
            entry["app"] = acl["app"]
        items[_object_key(items, s)] = {
            "entry": entry,
            "compare": {k: _text(v) for k, v in c.items() if not k.startswith("eai:") and k not in IGNORED_SAVED_SEARCH_FIELDS},
            "summary": {**acl, "disabled": _flag(c.get("disabled")), "scheduled": _flag(c.get("is_scheduled")),
                        "cron": c.get("cron_schedule") or "", "is_es": _is_es_use_case(s), "is_cu": is_cu_alert(s.name),
                        "is_test": is_test_alert(s.name)},
        }
    return items


def _phase_groups(items):
    """{clave sin «TEST - »: {"prod": clave, "test": clave}} de las saved searches de un cliente."""
    groups = {}
    for key in sorted(items):
        base = strip_test_prefix(key)
        groups.setdefault(base, {}).setdefault("test" if base != key else "prod", key)
    return groups


def pair_test_versions(data, reference):
    """
    Junta en una sola fila la versión TEST («TEST - CU-X») y la de producción («CU-X») de cada caso de uso. En cada cliente
    se compara la misma fase que tiene la referencia (la de producción si tiene las dos) o, si ese cliente no la tiene,
    la otra. Devuelve {cliente: {clave sin «TEST - »: objeto}} y cada objeto lleva su clave real ('key') y las fases que
    hay en ese cliente ('phases').
    """
    groups = {client: _phase_groups(items) for client, items in data.items()}
    reference_phase = {base: "prod" if "prod" in g else "test" for base, g in groups.get(reference, {}).items()}
    paired = {}
    for client, items in data.items():
        paired[client] = {}
        for base, g in groups[client].items():
            wanted = reference_phase.get(base, "prod")
            phase = wanted if wanted in g else next(iter(g))
            paired[client][base] = {**items[g[phase]], "key": g[phase], "phases": [p for p in ("prod", "test") if p in g]}
    return paired


def _macros(service):
    items = {}
    for m in all_macros(service):
        c = m.content
        items[_object_key(items, m)] = {
            "entry": macro_entry(m),   # incluye iseval, estado y descripción para que la copia funcione igual
            "compare": {"definition": _text(c.get("definition")), "args": _text(c.get("args")),
                        "iseval": _text(c.get("iseval")), "disabled": _text(c.get("disabled")),
                        "description": _text(c.get("description"))},
            "summary": {**acl_of(m), "disabled": _flag(c.get("disabled")), "definition": (c.get("definition") or "")[:160]},
        }
    return items


ROLE_FIELDS = ("capabilities", "imported_roles", "srchIndexesAllowed", "srchIndexesDefault", "srchJobsQuota",
               "srchDiskQuota", "srchTimeWin", "defaultApp")


def _roles(service):
    items = {}
    for r in service.roles:
        c = r.content
        items[r.name] = {
            "entry": {"name": r.name, "capabilities": c.get("capabilities", []), "imported_roles": c.get("imported_roles", []),
                      "srchIndexesAllowed": c.get("srchIndexesAllowed", []), "srchIndexesDefault": c.get("srchIndexesDefault", [])},
            "compare": {k: _text(c.get(k)) for k in ROLE_FIELDS},
            "summary": {"imported_roles": ", ".join(c.get("imported_roles") or [])},
        }
    return items


def _dashboards(service):
    data = _read_json(service, "/servicesNS/-/-/data/ui/views", count=0,
                      f=["label", "isDashboard", "isVisible", "eai:data"])
    items = {}
    for entry in data.get("entry", []):
        c = entry.get("content", {})
        if not _flag(c.get("isDashboard", True)):
            continue
        app = entry.get("acl", {}).get("app")
        items[_unique_key(items, entry.get("name"), app)] = {
            "entry": {"name": entry.get("name"), "eai:data": c.get("eai:data") or ""},
            "compare": {"label": _text(c.get("label")), "isVisible": _text(c.get("isVisible", True)),
                        "eai:data": _text(c.get("eai:data"))},
            "summary": {"app": app, "label": c.get("label") or entry.get("name")},
        }
    return items


def _indexes(service):
    return {r["name"]: {
        "entry": None,
        "compare": {"datatype": r["datatype"], "disabled": _text(r["disabled"]), "retention_days": _text(r["retention_days"]),
                    "max_size_mb": _text(r["max_size_mb"]), "archive_days": _text(r["archive_days"])},
        "summary": {"events": r["events"], "retention_days": r["retention_days"]},
    } for r in list_indexes(service)}


def _apps(service):
    return {a["name"]: {
        "entry": None,
        "compare": {"version": a["version"], "disabled": _text(a["disabled"]), "visible": _text(a["visible"]), "label": a["label"]},
        "summary": {"label": a["label"], "version": a["version"]},
    } for a in list_apps(service)}


def _users(service):
    return {u["name"]: {
        "entry": None,
        "compare": {"roles": _text(u["roles"]), "email": u["email"], "realname": u["realname"],
                    "default_app": u["default_app"], "type": u["type"]},
        "summary": {"realname": u["realname"], "roles": ", ".join(u["roles"])},
    } for u in list_users(service)}


TYPES = {
    "alerts": {"label": "Alertas", "fetch": lambda svc: _saved_searches(svc, True), "folder": "alertas", "main": MAIN_SAVED_SEARCH_FIELDS,
               "pair": True},
    "reports": {"label": "Reportes", "fetch": lambda svc: _saved_searches(svc, False), "folder": "reportes", "main": MAIN_SAVED_SEARCH_FIELDS,
                "pair": True},
    "macros": {"label": "Macros", "fetch": _macros, "folder": "macros"},
    "dashboards": {"label": "Dashboards", "fetch": _dashboards, "folder": "dashboards"},
    "roles": {"label": "Roles", "fetch": _roles, "folder": "roles"},
    "users": {"label": "Usuarios", "fetch": _users, "folder": None},
    "indexes": {"label": "Índices", "fetch": _indexes, "folder": None},
    "apps": {"label": "Apps", "fetch": _apps, "folder": None},
}


def types_meta():
    return {k: {"label": v["label"], "copy": bool(v["folder"]), "has_main": bool(v.get("main")), "pair": bool(v.get("pair"))}
            for k, v in TYPES.items()}


def _type(kind):
    if kind not in TYPES:
        raise OperationError(f"Tipo de objeto no válido: {kind}")
    return TYPES[kind]


def _fetch_all(kind, names):
    """Lee el tipo de objeto en varios clientes en paralelo. Devuelve ({cliente: items}, {cliente: error})."""
    fetch = _type(kind)["fetch"]

    def one(name):
        try:
            return name, fetch(services.get_service(name)), None
        except Exception as e:
            return name, None, str(e)

    data, errors = {}, {}
    with ThreadPoolExecutor(max_workers=min(FETCH_WORKERS, len(names))) as pool:
        for name, items, error in pool.map(one, names):
            if error is None:
                data[name] = items
            else:
                errors[name] = error
    return data, errors


def compare_clients(kind, reference, targets, fields="main", pair_test=False):
    """
    Compara 'reference' con cada cliente de 'targets'. Estado de cada objeto en cada cliente:
    same (igual), different (con diferencias), missing (falta en el cliente), extra (solo en el cliente, no en la
    referencia), absent (ni en la referencia ni en ese cliente) o error (no se pudo leer el cliente).
    Con pair_test (alertas y reportes) la versión TEST y la de producción de cada caso de uso van en la misma fila; cada
    elemento lleva entonces el nombre real en cada cliente ('keys', para copiar) y las fases que hay en él ('phases').
    """
    spec = _type(kind)
    targets = [t for t in dict.fromkeys(targets) if t and t != reference]
    if not reference or not targets:
        raise OperationError("Elija un cliente de referencia y al menos otro cliente para comparar.")
    main = spec.get("main") if fields == "main" else None
    pair_test = bool(pair_test and spec.get("pair"))

    data, errors = _fetch_all(kind, [reference] + targets)
    if reference in errors:
        raise OperationError(f"No se pudo leer {reference}: {errors[reference]}")
    if pair_test:
        data = pair_test_versions(data, reference)
    ref = data[reference]

    def projected(item):
        return {k: v for k, v in item["compare"].items() if k in main} if main else item["compare"]

    keys = set(ref)
    for target in targets:
        keys |= set(data.get(target, {}))

    items = []
    counts = {t: {"same": 0, "different": 0, "missing": 0, "extra": 0} for t in targets}
    for key in sorted(keys, key=str.lower):
        in_ref = key in ref
        source = ref.get(key) or next(data[t][key] for t in targets if key in data.get(t, {}))
        item = {"name": key, "in_reference": in_ref, "summary": source["summary"], "status": {}, "fields": {}}
        if pair_test:
            present = [c for c in [reference] + targets if key in data.get(c, {})]
            item["keys"] = {c: data[c][key]["key"] for c in present}
            item["phases"] = {c: data[c][key]["phases"] for c in present}
        for target in targets:
            if target in errors:
                item["status"][target] = "error"
                continue
            in_target = key in data[target]
            if in_ref and in_target:
                diff = diff_fields(projected(ref[key]), projected(data[target][key]))
                status = "different" if diff else "same"
                if diff:
                    item["fields"][target] = diff
            elif in_ref:
                status = "missing"
            elif in_target:
                status = "extra"
            else:
                status = "absent"
            item["status"][target] = status
            if status in counts[target]:
                counts[target][status] += 1
        items.append(item)

    return {
        "type": kind, "label": spec["label"], "reference": reference, "targets": targets, "fields": fields,
        "pair_test": pair_test, "can_copy": bool(spec["folder"]), "errors": errors, "counts": counts, "items": items,
    }


# --- Copia entre clientes ---------------------------------------------------------

def _write_temp(directory, name, data):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)
    return path


def _backup_existing(service, kind, folder, existing, keys):
    """Backup de lo que se va a sobrescribir en el destino (con historial), para poder deshacer la copia."""
    present = [k for k in keys if k in existing]
    if not present:
        return
    if folder == "dashboards":
        for key in present:
            backup_dashboard(service, existing[key]["entry"]["name"])
        return
    prefix = BACKUP_FOLDERS[folder]["prefix"]
    path = os.path.join(backup_dir(folder), f"{prefix}{COPY_LABEL}_{host_tag(service)}_backup.json")
    write_backup(path, [existing[k]["entry"] for k in present])
    print(f"Copia de seguridad previa: {len(present)} elementos que se van a sobrescribir guardados en {os.path.basename(path)}")


def build_copy(kind, source, targets, names, options):
    """Devuelve (título, función) para copiar objetos de 'source' a cada cliente de 'targets' en una tarea."""
    spec = _type(kind)
    folder = spec["folder"]
    if not folder:
        raise OperationError(f"Los objetos de tipo {spec['label'].lower()} no se pueden copiar.")
    names = [str(n) for n in dict.fromkeys(names or []) if str(n).strip()]
    targets = [t for t in dict.fromkeys(targets or []) if t and t != source]
    if not names:
        raise OperationError("No se ha seleccionado ningún elemento.")
    if not source or not targets:
        raise OperationError("Indique el cliente de origen y al menos un cliente de destino.")
    sharing = (options or {}).get("sharing") or "global"
    if sharing not in SHARING_LEVELS:
        raise OperationError("Nivel de compartición no válido.")
    restore = BACKUP_FOLDERS[folder]["restore"]
    opts = {"sharing": sharing, "initial_password": ""}

    def run():
        print(f"▶ Leyendo {spec['label'].lower()} de {source}…")
        source_items = spec["fetch"](services.get_service(source))
        missing = [n for n in names if n not in source_items]
        if missing:
            print(f"[ADVERTENCIA] No existen en {source}: {', '.join(missing)}")
        keys = [n for n in names if n in source_items]
        if not keys:
            print("❌ No hay nada que copiar.")
            return

        for target in targets:
            print(f"\n━━━━━━━━━━ {target} ━━━━━━━━━━")
            try:
                service = services.get_service(target)
                _backup_existing(service, kind, folder, spec["fetch"](service), keys)
            except Exception as e:
                print(f"❌ Error en {target}: {e}")
                continue
            temp_dir = tempfile.mkdtemp(prefix="splunk_manager_copy_")
            try:
                if folder == "dashboards":
                    for key in keys:
                        entry = source_items[key]["entry"]
                        restore(service, _write_temp(temp_dir, f"{entry['name']}_backup.json", {"eai:data": entry["eai:data"]}), opts)
                else:
                    entries = [source_items[k]["entry"] for k in keys]
                    restore(service, _write_temp(temp_dir, f"{BACKUP_FOLDERS[folder]['prefix']}copia.json", entries), opts)
            except Exception as e:
                print(f"❌ Error al copiar en {target}: {e}")
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

    what = f"{len(names)} {spec['label'].lower()}" if len(names) != 1 else f"«{names[0]}»"
    destination = targets[0] if len(targets) == 1 else f"{len(targets)} clientes"
    return f"Copia de {what} de {source} a {destination}", run
