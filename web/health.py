"""Panel de salud: comprobaciones rápidas de varios clientes en paralelo, con el último resultado en memoria."""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from web import services
from web.explorer import oneshot_results
from web.services import ClientError

CLIENT_WORKERS = 6

SCHEDULER_SPL = (
    'search index=_internal sourcetype=scheduler earliest=-24h '
    '| eval name=if(status=="success", "", savedsearch_name) '
    '| fillnull value="" name app reason '
    '| stats count by status name app reason'
)
INGEST_SPL = (
    'search index=_internal source=*license_usage.log* type=Usage earliest=-8d '
    '| eval period=if(_time >= relative_time(now(), "-24h"), "last", "prev") '
    '| stats sum(b) as bytes by period'
)
SOURCES_SPL = '| tstats latest(_time) as last count where index=* earliest=-7d by index sourcetype'


def _flag(value):
    return str(value).strip().lower() in ("1", "true", "yes")


def _alerts(service):
    """Recuento de alertas (incluidos casos de uso de ES) sin descargar toda su configuración."""
    response = service.get("saved/searches", output_mode="json", count=0,
                           f=["disabled", "alert_type", "is_scheduled", "tags"])
    entries = json.loads(response.body.read().decode("utf-8")).get("entry", [])
    summary = {"total": 0, "disabled": 0, "cu_total": 0, "cu_disabled": 0, "es": 0}
    for entry in entries:
        content = entry.get("content", {})
        app = entry.get("acl", {}).get("app")
        is_alert = (content.get("alert_type") or "none") != "none"
        is_es = app == "SplunkEnterpriseSecuritySuite" or "ESCU" in (content.get("tags") or "")
        if not (is_alert or is_es):
            continue
        disabled = _flag(content.get("disabled"))
        summary["total"] += 1
        summary["disabled"] += disabled
        summary["es"] += is_es
        if entry.get("name", "").startswith("CU-"):
            summary["cu_total"] += 1
            summary["cu_disabled"] += disabled
    return summary


def _scheduler(service):
    """Ejecuciones del planificador en las últimas 24 h y búsquedas con problemas."""
    totals = {"success": 0, "skipped": 0, "errors": 0, "other": 0}
    problems = []
    for row in oneshot_results(service, SCHEDULER_SPL):
        status = row.get("status") or ""
        count = int(float(row.get("count") or 0))
        if status == "success":
            totals["success"] += count
            continue
        kind = "skipped" if status == "skipped" else "errors" if ("error" in status or "fail" in status) else "other"
        totals[kind] += count
        if kind != "other":
            problems.append({"name": row.get("name") or "", "app": row.get("app") or "", "status": status,
                             "reason": row.get("reason") or "", "count": count})
    problems.sort(key=lambda p: p["count"], reverse=True)
    return {**totals, "problems": problems[:50]}


def _ingest(service):
    """Ingesta de las últimas 24 h frente a la media diaria de los 7 días anteriores (license_usage.log)."""
    periods = {row.get("period"): float(row.get("bytes") or 0) for row in oneshot_results(service, INGEST_SPL)}
    last = periods.get("last", 0.0)
    avg7 = periods.get("prev", 0.0) / 7
    return {"last24h_bytes": last, "avg7d_bytes": avg7, "change": (last - avg7) / avg7 if avg7 else None}


def _sources(service):
    """Último evento de cada índice/sourcetype con datos en los últimos 7 días."""
    rows = oneshot_results(service, SOURCES_SPL)
    return sorted(({"index": r.get("index"), "sourcetype": r.get("sourcetype"),
                    "last": float(r.get("last") or 0), "count": int(float(r.get("count") or 0))} for r in rows),
                  key=lambda r: r["last"])


CHECKS = {"alerts": _alerts, "scheduler": _scheduler, "ingest": _ingest, "sources": _sources}


def _run_check(check, service):
    started = time.time()
    try:
        return {"ok": True, "data": check(service), "seconds": round(time.time() - started, 1)}
    except Exception as e:
        return {"ok": False, "error": str(e), "seconds": round(time.time() - started, 1)}


def check_client(name):
    started = time.time()
    result = {"client": name, "started": started}
    try:
        service = services.connect(name)
    except ClientError as e:
        return {**result, "status": "error", "error": str(e), "finished": time.time()}

    with ThreadPoolExecutor(max_workers=len(CHECKS)) as pool:
        futures = {key: pool.submit(_run_check, check, service) for key, check in CHECKS.items()}
        checks = {key: future.result() for key, future in futures.items()}
    return {**result, "status": "done", "info": services.server_info(name), "checks": checks, "finished": time.time()}


# --- Diagnóstico (misma lógica que evaluate() de web/static/js/views/health.js) ------------------

RANK = {"pending": -1, "ok": 0, "info": 0, "warning": 1, "serious": 2, "critical": 3}
LEVEL_LABELS = {"ok": "Correcto", "info": "Info", "warning": "Aviso", "serious": "Problema", "critical": "Crítico",
                "pending": "Sin analizar"}
CHECK_LABELS = {"alerts": "las alertas", "scheduler": "el planificador", "ingest": "la ingesta",
                "sources": "las fuentes de datos"}


def _num(n):
    return f"{int(n):,}".replace(",", ".")


def _pct(n):
    return f"{round(n * 100)} %"


def source_key(client, source):
    return f"{client}|{source.get('index')}|{source.get('sourcetype')}"


def evaluate(result, threshold_hours=24, ignored=(), now=None):
    """Nivel global y lista de problemas de un cliente: {level, problems: [{level, text}], silent: [fuentes]}."""
    problems = []
    ignored = set(ignored or ())

    def add(level, text):
        problems.append({"level": level, "text": text})

    if not result or not result.get("checks"):
        if result and result.get("status") == "error":
            add("critical", f"Sin conexión: {result.get('error')}")
            return {"level": "critical", "problems": problems, "silent": []}
        return {"level": "pending", "problems": problems, "silent": []}
    if result.get("status") == "error":
        add("critical", f"Sin conexión: {result.get('error')}")

    checks = result["checks"]
    for key, check in checks.items():
        if not check.get("ok"):
            add("warning", f"No se pudo comprobar {CHECK_LABELS.get(key, key)}: {check.get('error')}")

    scheduler = checks.get("scheduler", {}).get("data") if checks.get("scheduler", {}).get("ok") else None
    if scheduler and scheduler.get("errors"):
        add("serious", f"{_num(scheduler['errors'])} ejecuciones del planificador con error en 24 h")
    if scheduler and scheduler.get("skipped"):
        add("warning", f"{_num(scheduler['skipped'])} ejecuciones del planificador saltadas en 24 h")

    silent = []
    if checks.get("sources", {}).get("ok"):
        limit = (now or time.time()) - threshold_hours * 3600
        silent = [s for s in checks["sources"]["data"]
                  if s["last"] < limit and source_key(result.get("client"), s) not in ignored]
        if silent:
            noun = "fuente sin datos" if len(silent) == 1 else "fuentes sin datos"
            add("serious", f"{len(silent)} {noun} en las últimas {threshold_hours} h")

    ingest = checks.get("ingest", {}).get("data") if checks.get("ingest", {}).get("ok") else None
    if ingest and ingest.get("avg7d_bytes", 0) > 0:
        if ingest.get("last24h_bytes") == 0:
            add("critical", "No ha entrado ningún dato en las últimas 24 h")
        elif ingest.get("change") is not None and ingest["change"] < -0.5:
            add("serious", f"La ingesta ha caído un {_pct(-ingest['change'])} respecto a la media de 7 días")
        elif ingest.get("change") is not None and ingest["change"] > 1:
            add("warning", f"La ingesta ha subido un {_pct(ingest['change'])} respecto a la media de 7 días")

    alerts = checks.get("alerts", {}).get("data") if checks.get("alerts", {}).get("ok") else None
    if alerts and alerts.get("cu_disabled"):
        n = alerts["cu_disabled"]
        add("info", f"{n} caso de uso CU- deshabilitado" if n == 1 else f"{n} casos de uso CU- deshabilitados")

    level = "ok"
    for p in problems:
        if RANK[p["level"]] > RANK[level]:
            level = p["level"]
    problems.sort(key=lambda p: RANK[p["level"]], reverse=True)
    return {"level": level, "problems": problems, "silent": silent}


class HealthMonitor:
    """Lanza las comprobaciones en segundo plano y guarda el último resultado de cada cliente."""

    def __init__(self):
        self._results = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=CLIENT_WORKERS, thread_name_prefix="health")

    def run(self, names):
        for name in names:
            with self._lock:
                previous = self._results.get(name, {})
                if previous.get("status") == "running":
                    continue
                # Se conserva el resultado anterior para mostrarlo mientras se actualiza
                self._results[name] = {**previous, "client": name, "status": "running", "started": time.time()}
            self._pool.submit(self._check, name)

    def _check(self, name):
        try:
            result = check_client(name)
        except Exception as e:
            result = {"client": name, "status": "error", "error": f"{type(e).__name__}: {e}", "finished": time.time()}
        with self._lock:
            self._results[name] = result

    def store(self, name, result):
        """Guarda un resultado obtenido fuera del monitor (p. ej. al generar un informe)."""
        with self._lock:
            if self._results.get(name, {}).get("status") != "running":
                self._results[name] = result

    def snapshot(self):
        with self._lock:
            results = dict(self._results)
        return {"results": results, "running": any(r.get("status") == "running" for r in results.values())}


monitor = HealthMonitor()
