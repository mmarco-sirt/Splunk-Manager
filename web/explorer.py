"""Consultas y acciones en vivo sobre Splunk para la interfaz web (lo que no es backup/restore)."""
import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

from splunklib.binding import HTTPError
from splunklib.client import Job

from core.conventions import is_cu_alert, is_test_alert, strip_test_prefix
from core.knowledge import all_macros, all_saved_searches, find_saved_search
from options.alerts.backup_alerts import _is_alert, _is_es_use_case

DEFAULT_MAX_RESULTS = 1000
CLOUD_INDEXES_PATH = "/services/cluster_blaster_indexes/sh_indexes_manager"
EVENTCOUNT_SPL = "| eventcount summarize=false index=* index=_* | stats sum(count) as count by index"


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


def oneshot_results(service, spl, **params):
    """Ejecuta una búsqueda corta (oneshot) y devuelve sus resultados; lanza excepción si Splunk da error."""
    body = service.jobs.oneshot(spl, output_mode="json", count=0, **params).read()
    data = json.loads(body.decode("utf-8")) if body else {}
    results = data.get("results", [])
    errors = [m.get("text", "") for m in data.get("messages", []) if m.get("type") in ("FATAL", "ERROR")]
    if errors and not results:
        raise RuntimeError("; ".join(errors))
    return results


# --- Saved searches (alertas y reportes) ---

def saved_search_row(s):
    """Resumen de una saved search para los listados de la web."""
    c = s.content
    return {
        "name": s.name,
        **_acl(s),
        "disabled": _flag(c.get("disabled")),
        "scheduled": _flag(c.get("is_scheduled")),
        "cron": c.get("cron_schedule") or "",
        "next_run": c.get("next_scheduled_time") or "",
        "alert_type": c.get("alert_type") or "",
        "is_alert": _is_alert(s),
        "is_es": _is_es_use_case(s),
        "is_cu": is_cu_alert(s.name),         # CU-… y también su versión de pruebas TEST - CU-…
        "is_test": is_test_alert(s.name),
        "base_name": strip_test_prefix(s.name),
        "es_label": c.get("action.correlationsearch.label") or "",
        "severity": c.get("alert.severity") or "",
        "actions": c.get("actions") or "",
        "search": c.get("search") or "",
        "description": c.get("description") or "",
        "earliest": c.get("dispatch.earliest_time") or "",
        "latest": c.get("dispatch.latest_time") or "",
    }


def list_saved_searches(service):
    """Saved searches de todas las apps y usuarios (también las privadas y las compartidas solo en su app)."""
    return [saved_search_row(s) for s in all_saved_searches(service)]


def saved_search(service, name, app, owner, sharing=None):
    """Saved search por nombre (en su app/propietario/compartición si se indican). KeyError si no existe."""
    return _saved_search(service, name, app, owner, sharing)


def _saved_search(service, name, app, owner, sharing=None):
    if app and owner:
        return find_saved_search(service, name, app, owner, sharing)
    return service.saved_searches[name]


def saved_search_action(service, name, app, owner, action, sharing=None):
    """Acciones sobre una saved search: enable, disable o dispatch (ejecutar ahora)."""
    saved_search = _saved_search(service, name, app, owner, sharing)
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

def macro_row(m):
    """Resumen de una macro para los listados de la web."""
    return {
        "name": m.name,
        **_acl(m),
        "definition": m.content.get("definition") or "",
        "args": m.content.get("args") or "",
        "iseval": _flag(m.content.get("iseval")),
        "disabled": _flag(m.content.get("disabled")),
        "description": m.content.get("description") or "",
    }


def list_macros(service):
    """Macros de todas las apps y usuarios (también las privadas y las compartidas solo en su app)."""
    return [macro_row(m) for m in all_macros(service)]


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


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _epoch(value):
    """minTime/maxTime de Splunk (epoch o ISO 8601) como segundos epoch, o None."""
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except ValueError:
        return None


def _local_indexes(service):
    return {index.name: index.content for index in service.indexes.list(datatype="all")}


def _cloud_indexes(service):
    """
    Índices de Splunk Cloud con los datos reales de los indexers. En Cloud, /services/data/indexes del
    search head solo devuelve copias locales vacías (1 MB, 0 eventos). Devuelve None si no es Splunk Cloud.
    """
    try:
        data = _read_json(service, CLOUD_INDEXES_PATH, count=0)
    except HTTPError as e:
        if e.status in (403, 404):
            return None
        raise
    return {entry["name"]: entry["content"] for entry in data.get("entry", [])}


def _event_counts(service):
    """Eventos por índice según los indexers (| eventcount), incluidos los internos. {} si no se puede."""
    try:
        results = oneshot_results(service, EVENTCOUNT_SPL)
    except Exception:
        return {}
    return {r["index"]: int(_number(r.get("count"))) for r in results if r.get("index")}


def list_indexes(service):
    with ThreadPoolExecutor(max_workers=3) as pool:
        local_job = pool.submit(_local_indexes, service)
        cloud_job = pool.submit(_cloud_indexes, service)
        counts_job = pool.submit(_event_counts, service)
        local, cloud, counts = local_job.result(), cloud_job.result(), counts_job.result()

    rows = []
    for name in sorted(set(local) | set(cloud or {})):
        cloud_info = (cloud or {}).get(name)
        info = cloud_info or local.get(name, {})
        if cloud is not None:
            # Splunk Cloud: tamaño de los datos sin comprimir; los índices internos no lo exponen
            size_mb = _number(cloud_info.get("totalRawSizeMB")) if cloud_info else None
            max_size_mb = _number(cloud_info.get("maxGlobalDataSizeMB")) if cloud_info else 0
        else:
            size_mb = _number(info.get("currentDBSizeMB"))
            max_size_mb = _number(info.get("maxTotalDataSizeMB"))
        archive_days = 0
        if cloud_info and _flag(cloud_info.get("archiver.enableDataArchive")):
            archive_days = int(_number(cloud_info.get("archiver.coldStorageRetentionPeriod")))
        events = counts.get(name)
        rows.append({
            "name": name,
            "datatype": info.get("datatype") or "event",
            "disabled": _flag(info.get("disabled")),
            "events": events if events is not None else int(_number(info.get("totalEventCount"))),
            "size_mb": size_mb,
            "size_kind": "raw" if cloud is not None else "disk",
            "max_size_mb": max_size_mb,
            "retention_days": round(_number(info.get("frozenTimePeriodInSecs")) / 86400, 1),
            "archive_days": archive_days,
            "min_time": _epoch(info.get("minTime")),
            "max_time": _epoch(info.get("maxTime")),
        })
    return rows


INGEST_DAYS = (7, 30, 90)
INGEST_SPL = (
    'search index=_internal source=*license_usage.log* type=Usage earliest=-{offset}d@d '
    '| bin _time span=1d | stats sum(b) as bytes by _time idx st'
)
# Entitlements de la suscripción de Splunk Cloud: la misma búsqueda que el panel License Usage > Entitlement de la
# Cloud Monitoring Console, con sus macros (por eso se lanza en el contexto de su app; requiere sc_admin).
# Todos los valores vienen en GB; vacío o 0 es N/A (p. ej. sin DDAA, o licencia por SVC en lugar de por ingesta).
CMC_APP = "splunk_instance_monitoring"
ENTITLEMENTS_EARLIEST = "-30d"
_LOCAL_STACK = '[`sim_get_local_stack` | eval host="*.".stack.".*splunk*" | fields host]'
ENTITLEMENTS_SPL = (
    f'search `sim_set_index_cmc_summary` source="splunk-entitlements" {_LOCAL_STACK} '
    '| stats latest(ingest_license) as ingest '
    f'| appendcols [search `sim_set_index_cmc_summary` source="splunk-storage-summary" {_LOCAL_STACK} '
    '| stats latest(activeStorageLicenseGB) as ddas latest(activeArchiveLicenseGB) as ddaa '
    'latest(DDAARestoreLimitGB) as restore]'
)
GB = 1024 ** 3


def _usage_summary(series_by_name, extra=None):
    """[{name, total, avg_day, last_day, today, series}] ordenado por volumen. 'avg_day' excluye hoy (parcial)."""
    rows = []
    for name, series in series_by_name.items():
        complete = series[:-1]
        row = {
            "name": name,
            "total": sum(series),
            "avg_day": sum(complete) / len(complete) if complete else 0,
            "last_day": complete[-1] if complete else 0,
            "today": series[-1],
            "series": series,
        }
        if extra:
            row.update(extra(name))
        rows.append(row)
    return sorted(rows, key=lambda r: r["total"], reverse=True)


def cloud_entitlements(service):
    """
    Entitlements de Splunk Cloud en GB: ingest (licencia diaria), ddas, ddaa y restore (None si no aplica).
    Si no se pueden leer, 'error' lleva el motivo y el resto va a None.
    """
    keys = ("ingest", "ddas", "ddaa", "restore")
    try:
        results = oneshot_results(service, ENTITLEMENTS_SPL, app=CMC_APP, owner="nobody",
                                  earliest_time=ENTITLEMENTS_EARLIEST)
    except HTTPError as e:
        error = ("La cuenta no tiene acceso a la Cloud Monitoring Console (hace falta el rol sc_admin)."
                 if e.status in (403, 404) else f"Splunk respondió HTTP {e.status} {e.reason}.")
        return {**dict.fromkeys(keys), "error": error}
    except Exception as e:
        return {**dict.fromkeys(keys), "error": f"{type(e).__name__}: {e}"}
    row = results[0] if results else {}
    values = {key: _number(row.get(key)) or None for key in keys}
    error = None if any(values.values()) else "La Cloud Monitoring Console no tiene entitlements registrados."
    return {**values, "error": error}


def ingest_usage(service, days=30):
    """Ingesta diaria (bytes de licencia) por índice y sourcetype de los últimos 'days' días, hoy incluido."""
    if days not in INGEST_DAYS:
        raise ValueError(f"Periodo no válido: {days} días.")
    today = date.today()
    day_keys = [(today - timedelta(days=offset)).isoformat() for offset in range(days - 1, -1, -1)]
    position = {day: i for i, day in enumerate(day_keys)}

    with ThreadPoolExecutor(max_workers=2) as pool:
        usage_job = pool.submit(oneshot_results, service, INGEST_SPL.format(offset=days - 1))
        entitlements_job = pool.submit(cloud_entitlements, service)
        usage, entitlements = usage_job.result(), entitlements_job.result()

    totals = [0] * days
    by_index = defaultdict(lambda: [0] * days)
    by_sourcetype = defaultdict(lambda: [0] * days)
    sourcetype_indexes = defaultdict(set)
    for row in usage:
        i = position.get(str(row.get("_time", ""))[:10])
        if i is None:
            continue
        size = int(_number(row.get("bytes")))
        index, sourcetype = row.get("idx") or "(sin índice)", row.get("st") or "(sin sourcetype)"
        totals[i] += size
        by_index[index][i] += size
        by_sourcetype[sourcetype][i] += size
        sourcetype_indexes[sourcetype].add(index)

    complete = totals[:-1]
    quota = entitlements["ingest"] * GB if entitlements["ingest"] else None
    peak =max(range(len(complete)), key=lambda i: complete[i]) if complete else None
    return {
        "days": day_keys,
        "totals": totals,
        "total": sum(totals),
        "avg_day": sum(complete) / len(complete) if complete else 0,
        "peak": {"day": day_keys[peak], "bytes": complete[peak]} if peak is not None else None,
        # Entitlements y días completos del periodo por encima de la licencia de ingesta
        "license": {**entitlements, "over_days": sum(1 for b in complete if b > quota) if quota else None},
        "indexes": _usage_summary(by_index),
        "sourcetypes": _usage_summary(by_sourcetype, lambda st: {"indexes": sorted(sourcetype_indexes[st])}),
    }


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
