"""
Cobertura de índices de un cliente: lee de Splunk las alertas, macros, eventtypes, tags, data models, índices y los
eventos de los últimos días por índice y sourcetype, y lo analiza con core.index_coverage.
"""
from concurrent.futures import ThreadPoolExecutor

from core.index_coverage import (
    DATA_DAYS, MODE_LABELS, STATUS_LABELS, Knowledge, analyze, ignored_rules, parse_datamodel,
)
from core.knowledge import ALL_NAMESPACES, acl_of, all_saved_searches
from core.conventions import is_cu_alert
from web.dependencies import macro_records
from web.explorer import _cloud_indexes, _flag, _local_indexes, _read_json, oneshot_results

# tstats con index=* no incluye los índices internos (_*), que de todas formas se excluyen
DATA_SPL = "| tstats count max(_time) as last where index=* by index sourcetype"
KIND_LABELS = {"active": "Habilitado", "disabled": "Deshabilitado", "test": "Pruebas (TEST)"}


def _saved_searches(service):
    rows = []
    for s in all_saved_searches(service):
        if not is_cu_alert(s.name):
            continue
        rows.append({"name": s.name, **acl_of(s), "disabled": _flag(s.content.get("disabled")),
                     "search": s.content.get("search") or ""})
    return rows


def _eventtypes(service):
    data = _read_json(service, "saved/eventtypes", count=0, f=["search", "disabled"], **ALL_NAMESPACES)
    rows = []
    for entry in data.get("entry", []):
        c, acl = entry.get("content") or {}, entry.get("acl") or {}
        rows.append({"name": entry.get("name"), "app": acl.get("app"), "owner": acl.get("owner"),
                     "sharing": acl.get("sharing"), "search": c.get("search") or "", "disabled": _flag(c.get("disabled"))})
    return rows


def _tags(service):
    data = _read_json(service, "configs/conf-tags", count=0, **ALL_NAMESPACES)
    rows = []
    for entry in data.get("entry", []):
        c = entry.get("content") or {}
        if _flag(c.get("disabled")):
            continue
        tags = [k for k, v in c.items() if not k.startswith("eai:") and str(v).strip().lower() == "enabled"]
        if tags:
            rows.append({"name": entry.get("name"), "tags": tags})
    return rows


def _datamodels(service):
    data = _read_json(service, "datamodel/model", count=0, **ALL_NAMESPACES)
    models = {}
    for entry in data.get("entry", []):
        c = entry.get("content") or {}
        if _flag(c.get("disabled")):
            continue
        # La definición JSON llega en eai:data o, en algunas versiones, en 'description'
        objects = parse_datamodel(c.get("eai:data") or "") or parse_datamodel(c.get("description") or "")
        if objects and entry.get("name") not in models:
            models[entry["name"]] = objects
    return models


def _indexes(service):
    cloud = _cloud_indexes(service)
    source = cloud if cloud is not None else _local_indexes(service)
    return [{"name": name, "datatype": (info or {}).get("datatype") or "event", "disabled": _flag((info or {}).get("disabled"))}
            for name, info in source.items()]


def _optional(fn, service, warnings, what):
    """Los objetos de conocimiento que no se puedan leer no impiden el análisis: se avisa y se sigue sin ellos."""
    try:
        return fn(service)
    except Exception as e:
        warnings.append(f"No se pudieron leer los {what}: {e}")
        return [] if what != "data models" else {}


def analyze_client(service, client_name=None):
    """Cobertura de índices de un cliente (ver core.index_coverage.analyze), con las reglas de ignorados del cliente."""
    warnings = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs = {
            "searches": pool.submit(_saved_searches, service),
            "macros": pool.submit(_optional, macro_records, service, warnings, "macros"),
            "eventtypes": pool.submit(_optional, _eventtypes, service, warnings, "eventtypes"),
            "tags": pool.submit(_optional, _tags, service, warnings, "tags"),
            "datamodels": pool.submit(_optional, _datamodels, service, warnings, "data models"),
            "indexes": pool.submit(_indexes, service),
            "data": pool.submit(oneshot_results, service, DATA_SPL, earliest_time=f"-{DATA_DAYS}d", latest_time="now"),
        }
        r = {key: job.result() for key, job in jobs.items()}
    knowledge = Knowledge(r["macros"], r["eventtypes"], r["tags"], r["datamodels"])
    result = analyze(r["searches"], r["indexes"], r["data"], knowledge, ignored_rules(client_name) if client_name else [])
    result["warnings"] = warnings
    result["knowledge"] = {"macros": len(r["macros"]), "eventtypes": len(r["eventtypes"]), "tags": len(r["tags"]),
                           "datamodels": len(r["datamodels"])}
    return result


def describe_users(row, limit=None):
    """'CU-A (vía), CU-B…' de los casos de uso que consultan un índice."""
    users = row["used_by"][:limit] if limit else row["used_by"]
    parts = []
    for u in users:
        kind = "" if u["kind"] == "active" else f" [{KIND_LABELS[u['kind']].lower()}]"
        parts.append(f"{u['name']}{kind}" + (f" (vía {u['via']})" if u["via"] else ""))
    if limit and len(row["used_by"]) > limit:
        parts.append(f"… y {len(row['used_by']) - limit} más")
    return parts


def report_data(result):
    """Lo que necesitan los informes (Excel/HTML) y el CLI: resumen, índices no cubiertos y CU de alcance amplio."""
    uncovered = [r for r in result["indexes"] if r["status"] in ("unused", "inactive_only")]
    broad = [a for a in result["alerts"] if a["kind"] == "active" and a["mode"] in ("all", "default")]
    return {
        "period_days": result["period_days"], "summary": result["summary"],
        "uncovered": [{**r, "status_label": STATUS_LABELS[r["status"]], "users_text": describe_users(r),
                       "sourcetypes_text": ", ".join(s["name"] for s in r["sourcetypes"][:10])} for r in uncovered],
        "ignored": [r for r in result["indexes"] if r["status"] == "ignored"],
        "broad": [{**a, "mode_label": MODE_LABELS[a["mode"]]} for a in broad],
        "issues": [a for a in result["alerts"] if a["kind"] == "active" and a["issues"]],
        "warnings": result.get("warnings") or [],
    }
