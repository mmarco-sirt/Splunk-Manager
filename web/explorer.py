"""Consultas y acciones en vivo sobre Splunk para la interfaz web (lo que no es backup/restore)."""
import json

from splunklib.binding import namespace
from splunklib.client import Job

from options.alerts.backup_alerts import _is_alert, _is_es_use_case

DEFAULT_MAX_RESULTS = 1000


def _flag(value):
    return str(value).strip().lower() in ("1", "true", "yes")


def _as_list(value):
    if value is None or value == "":
        return []
    return value if isinstance(value, list) else [value]


def _acl(entity):
    access = entity.access
    return {"app": access.get("app"), "owner": access.get("owner"), "sharing": access.get("sharing")}


def _read_json(service, path, **query):
    response = service.get(path, output_mode="json", **query)
    return json.loads(response.body.read().decode("utf-8"))


# --- Saved searches (alertas y reportes) ---

def list_saved_searches(service):
    rows = []
    for s in service.saved_searches:
        c = s.content
        rows.append({
            "name": s.name,
            **_acl(s),
            "disabled": _flag(c.get("disabled")),
            "scheduled": _flag(c.get("is_scheduled")),
            "cron": c.get("cron_schedule") or "",
            "next_run": c.get("next_scheduled_time") or "",
            "alert_type": c.get("alert_type") or "",
            "is_alert": _is_alert(s),
            "is_es": _is_es_use_case(s),
            "is_cu": s.name.startswith("CU-"),
            "es_label": c.get("action.correlationsearch.label") or "",
            "severity": c.get("alert.severity") or "",
            "actions": c.get("actions") or "",
            "search": c.get("search") or "",
            "description": c.get("description") or "",
            "earliest": c.get("dispatch.earliest_time") or "",
            "latest": c.get("dispatch.latest_time") or "",
        })
    return rows


def _saved_search(service, name, app, owner):
    if app and owner:
        return service.saved_searches[name, namespace(owner=owner, app=app)]
    return service.saved_searches[name]


def saved_search_action(service, name, app, owner, action):
    """Acciones sobre una saved search: enable, disable o dispatch (ejecutar ahora)."""
    saved_search = _saved_search(service, name, app, owner)
    if action == "enable":
        saved_search.enable()
        return {"message": f"'{name}' habilitada."}
    if action == "disable":
        saved_search.disable()
        return {"message": f"'{name}' deshabilitada."}
    if action == "dispatch":
        job = saved_search.dispatch()
        return {"message": f"'{name}' lanzada.", "sid": job.sid}
    raise ValueError(f"Acción no soportada: {action}")


# --- Otros objetos ---

def list_macros(service):
    return [{
        "name": m.name,
        **_acl(m),
        "definition": m.content.get("definition") or "",
        "args": m.content.get("args") or "",
        "iseval": _flag(m.content.get("iseval")),
        "disabled": _flag(m.content.get("disabled")),
        "description": m.content.get("description") or "",
    } for m in service.macros]


def list_users(service):
    return [{
        "name": u.name,
        "realname": u.content.get("realname") or "",
        "email": u.content.get("email") or "",
        "roles": _as_list(u.content.get("roles")),
        "type": u.content.get("type") or "",
        "default_app": u.content.get("defaultApp") or "",
        "locked": _flag(u.content.get("locked-out")),
        "last_login": u.content.get("last_successful_login") or "",
    } for u in service.users]


def list_roles(service):
    return [{
        "name": r.name,
        "imported_roles": _as_list(r.content.get("imported_roles")),
        "capabilities": _as_list(r.content.get("capabilities")),
        "imported_capabilities": _as_list(r.content.get("imported_capabilities")),
        "indexes_allowed": _as_list(r.content.get("srchIndexesAllowed")),
        "indexes_default": _as_list(r.content.get("srchIndexesDefault")),
        "jobs_quota": r.content.get("srchJobsQuota") or "",
        "disk_quota": r.content.get("srchDiskQuota") or "",
        "time_window": r.content.get("srchTimeWin") or "",
        "default_app": r.content.get("defaultApp") or "",
    } for r in service.roles]


def list_dashboards(service):
    # Se consulta en todos los namespaces y sin 'eai:data' (el XML), que puede ser muy pesado
    data = _read_json(service, "/servicesNS/-/-/data/ui/views", count=0,
                      f=["label", "isDashboard", "isVisible", "version", "rootNode", "eai:type"])
    rows = []
    for entry in data.get("entry", []):
        c = entry.get("content", {})
        if not _flag(c.get("isDashboard", True)):
            continue
        acl = entry.get("acl", {})
        rows.append({
            "name": entry.get("name"),
            "label": c.get("label") or entry.get("name"),
            "app": acl.get("app"),
            "owner": acl.get("owner"),
            "sharing": acl.get("sharing"),
            "visible": _flag(c.get("isVisible", True)),
            "type": c.get("eai:type") or "",
            "version": c.get("version") or "",
            "updated": entry.get("updated") or "",
        })
    return rows


def list_indexes(service):
    rows = []
    for index in service.indexes.list(datatype="all"):
        c = index.content
        rows.append({
            "name": index.name,
            "datatype": c.get("datatype") or "event",
            "disabled": _flag(c.get("disabled")),
            "events": int(float(c.get("totalEventCount") or 0)),
            "size_mb": float(c.get("currentDBSizeMB") or 0),
            "max_size_mb": float(c.get("maxTotalDataSizeMB") or 0),
            "retention_days": round(float(c.get("frozenTimePeriodInSecs") or 0) / 86400, 1),
            "min_time": c.get("minTime") or "",
            "max_time": c.get("maxTime") or "",
        })
    return rows


def list_apps(service):
    return [{
        "name": a.name,
        "label": a.content.get("label") or a.name,
        "version": a.content.get("version") or "",
        "author": a.content.get("author") or "",
        "disabled": _flag(a.content.get("disabled")),
        "visible": _flag(a.content.get("visible")),
        "description": a.content.get("description") or "",
    } for a in service.apps]


def list_fired_alerts(service):
    rows = [{"name": g.name, "count": g.count} for g in service.fired_alerts if g.name != "-"]
    return sorted(rows, key=lambda r: r["count"], reverse=True)


# --- Búsquedas SPL ---

def start_search(service, query, earliest=None, latest=None):
    query = (query or "").strip()
    if not query:
        raise ValueError("La búsqueda está vacía.")
    if not query.startswith("|") and not query.lower().startswith("search "):
        query = f"search {query}"
    params = {"exec_mode": "normal"}
    if earliest:
        params["earliest_time"] = earliest
    if latest:
        params["latest_time"] = latest
    job = service.jobs.create(query, **params)
    return job.sid


def search_status(service, sid, max_results=DEFAULT_MAX_RESULTS):
    """Estado de un job de búsqueda y, si ha terminado, sus resultados."""
    job = Job(service, sid)
    job.refresh()
    c = job.content
    status = {
        "sid": sid,
        "query": c.get("search") or "",
        "state": c.get("dispatchState") or "",
        "done": _flag(c.get("isDone")),
        "failed": _flag(c.get("isFailed")),
        "progress": float(c.get("doneProgress") or 0),
        "result_count": int(float(c.get("resultCount") or 0)),
        "event_count": int(float(c.get("eventCount") or 0)),
        "scan_count": int(float(c.get("scanCount") or 0)),
        "duration": float(c.get("runDuration") or 0),
        "messages": _job_messages(c.get("messages")),
    }
    if status["done"] and not status["failed"]:
        body = job.results(output_mode="json", count=max_results).read()
        data = json.loads(body.decode("utf-8")) if body else {}
        status["fields"] = [f["name"] if isinstance(f, dict) else f for f in data.get("fields", [])]
        status["results"] = data.get("results", [])
        status["messages"] += [m.get("text", "") for m in data.get("messages", []) if m.get("text")]
    return status


def _job_messages(messages):
    if not messages:
        return []
    if isinstance(messages, dict):
        return [f"{level}: {text}" for level, values in messages.items() for text in _as_list(values)]
    return [str(m) for m in _as_list(messages)]


def search_control(service, sid, action):
    job = Job(service, sid)
    if action == "cancel":
        job.cancel()
    elif action == "finalize":
        job.finalize()
    else:
        raise ValueError(f"Acción no soportada: {action}")
