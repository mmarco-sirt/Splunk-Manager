"""
Análisis de alertas a partir del log del planificador (index=_internal sourcetype=scheduler), para afinar casos de uso:

  - ruidosas: disparan de media NOISY_PER_DAY o más veces al día, o en casi todas sus ejecuciones
  - muertas: habilitadas pero que nunca disparan, no llegan a ejecutarse, el planificador las salta siempre o fallan
    siempre (o que no están programadas)
  - planificador: alertas que se saltan a menudo, con errores o que se ejecutan mucho menos de lo que indica su cron
  - tendencia: disparos por día y comparación de la media de la segunda mitad del periodo con la de la primera

Un «disparo» es una ejecución que lanzó acciones (email, notable, webhook...); las que suprime el throttle se cuentan
aparte. Una alerta sin acciones no registra disparos, así que se señala como «sin acciones». Las medias y la tendencia
usan solo días completos (sin hoy).
"""
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from core.knowledge import all_saved_searches
from options.alerts.backup_alerts import _is_alert, _is_es_use_case
from web.explorer import _flag, oneshot_results, saved_search_row

ANALYSIS_DAYS = (7, 14, 30)       # _internal suele guardar 30 días
NOISY_PER_DAY = 10
ALWAYS_FIRES_RATE = 0.8
ALWAYS_FIRES_MIN_RUNS = 5
SKIP_RATE = 0.2
MISSING_RUNS_RATE = 0.5
MISSING_RUNS_MIN = 4
TREND_CHANGE = 0.25
MAX_SKIP_REASONS = 5

SCHEDULER_SPL = (
    'search index=_internal sourcetype=scheduler savedsearch_name=* earliest=-{offset}d@d '
    '| fields _time savedsearch_name app status alert_actions suppressed result_count run_time reason '
    '| fillnull value="" app '
    '| eval ok=if(status=="success" OR status=="completed" OR status=="delegated_remote_completion", 1, 0), '
    'skip=if(status=="skipped", 1, 0), '
    'err=if(match(status, "(?i)error|fail"), 1, 0), '
    'supp=if(ok=1 AND coalesce(tonumber(suppressed), 0)=1, 1, 0), '
    'fired=if(ok=1 AND supp=0 AND len(coalesce(alert_actions, ""))>0, 1, 0), '
    'res=if(fired=1, coalesce(tonumber(result_count), 0), 0), '
    'rt=if(ok=1, tonumber(run_time), null()), '
    'skip_reason=if(skip=1, reason, null()) '
    '| bin _time span=1d '
    '| stats sum(ok) as runs sum(fired) as fired sum(supp) as suppressed sum(skip) as skipped sum(err) as errors '
    'sum(res) as results sum(rt) as runtime values(skip_reason) as skip_reasons by _time savedsearch_name app'
)

DEAD_LABELS = {
    "never_fired": "Nunca dispara",
    "no_actions": "Sin acciones: aunque dispare no avisa",
    "never_ran": "No se ha ejecutado",
    "always_skipped": "El planificador la salta siempre",
    "always_failed": "Falla siempre",
    "no_schedule": "Habilitada pero no programada",
}
SCHEDULER_DEAD = ("never_ran", "always_skipped", "always_failed")


class AnalysisError(ValueError):
    """El análisis no se puede hacer (mensaje apto para mostrar tal cual)."""


STATE_LABELS = {"active": "Activa", "disabled": "Deshabilitada", "unscheduled": "No programada", "realtime": "Tiempo real"}


def period_for(days):
    """Periodo de análisis más largo que no supera 'days' (los informes usan 7, 30 o 90 días de ingesta)."""
    return max([d for d in ANALYSIS_DAYS if d <= days] or [ANALYSIS_DAYS[0]])


# --- Cron: ejecuciones esperadas ------------------------------------------------------

def _cron_values(text, low, high):
    """Valores de un campo cron ('*', '*/5', '1,15', '1-5', '0-30/10'); None si no se entiende."""
    values = set()
    for part in text.split(","):
        step = 1
        if "/" in part:
            part, step_text = part.split("/", 1)
            if not step_text.isdigit() or int(step_text) == 0:
                return None
            step = int(step_text)
        if part == "*":
            start, end = low, high
        elif "-" in part:
            first, last = part.split("-", 1)
            if not (first.isdigit() and last.isdigit()):
                return None
            start, end = int(first), int(last)
        elif part.isdigit():
            start = int(part)
            end = high if step > 1 else start
        else:
            return None
        if start < low or end > high or start > end:
            return None
        values.update(range(start, end + 1, step))
    return values


def expected_runs(cron, days):
    """Ejecuciones que indica el cron en esos días (lista de date). None si el cron no se entiende."""
    parts = str(cron or "").split()
    if len(parts) != 5:
        return None
    minute, hour, dom, month, dow = parts
    fields = (_cron_values(minute, 0, 59), _cron_values(hour, 0, 23), _cron_values(dom, 1, 31),
              _cron_values(month, 1, 12), _cron_values(dow, 0, 7))
    if any(f is None for f in fields):
        return None
    minutes, hours, doms, months, dows = fields
    dows = {d % 7 for d in dows}   # 0 y 7 = domingo
    per_day = len(minutes) * len(hours)
    any_dom, any_dow = dom.startswith("*"), dow.startswith("*")
    total = 0
    for day in days:
        if day.month not in months:
            continue
        dom_ok, dow_ok = day.day in doms, (day.weekday() + 1) % 7 in dows
        # Como en cron: si se restringen día del mes y día de la semana, basta con que coincida uno de los dos
        if any_dom and any_dow:
            ok = True
        elif any_dom:
            ok = dow_ok
        elif any_dow:
            ok = dom_ok
        else:
            ok = dom_ok or dow_ok
        total += per_day if ok else 0
    return total


# --- Clasificación ------------------------------------------------------------------------

def trend(series):
    """Media diaria de disparos de la segunda mitad del periodo (días completos) frente a la primera."""
    complete = series[:-1]
    if len(complete) < 2:
        return None
    half = len(complete) // 2
    first, second = complete[:half], complete[half:]
    before, after = sum(first) / len(first), sum(second) / len(second)
    if not before and not after:
        return {"direction": "none", "change": None, "before": 0, "after": 0}
    if not before:
        return {"direction": "new", "change": None, "before": 0, "after": after}
    change = (after - before) / before
    direction = "up" if change > TREND_CHANGE else "down" if change < -TREND_CHANGE else "flat"
    return {"direction": direction, "change": change, "before": before, "after": after}


def noisy_reason(stats, per_day=NOISY_PER_DAY):
    """'per_day' (muchos disparos al día), 'always' (dispara en casi todas sus ejecuciones) o None."""
    if stats["fired_per_day"] >= per_day:
        return "per_day"
    if stats["runs"] >= ALWAYS_FIRES_MIN_RUNS and (stats["fire_rate"] or 0) >= ALWAYS_FIRES_RATE:
        return "always"
    return None


def _dead_reason(stats, has_actions):
    if stats["runs"] == 0:
        if stats["errors"]:
            return "always_failed" if stats["errors"] >= stats["skipped"] else "always_skipped"
        if stats["skipped"]:
            return "always_skipped"
        # Sin ejecuciones: solo es un problema si al cron le tocaba ejecutarse en el periodo
        return "never_ran" if stats["expected"] is None or stats["expected"] > 0 else None
    if not stats["fired"] and not stats["suppressed"]:
        # Sin acciones el planificador no registra los disparos: lo relevante es que la alerta no avisa a nadie
        return "never_fired" if has_actions else "no_actions"
    return None


def _issues(stats):
    issues = []
    attempts = stats["runs"] + stats["skipped"] + stats["errors"]
    if stats["runs"] and attempts and stats["skipped"] / attempts >= SKIP_RATE:
        issues.append("skips")
    if stats["runs"] and stats["errors"]:
        issues.append("errors")
    expected = stats["expected"]
    if expected and expected >= MISSING_RUNS_MIN and stats["runs"] and stats["attempts_complete"] < expected * MISSING_RUNS_RATE:
        issues.append("missing_runs")
    return issues


def _state(row):
    if row["disabled"]:
        return "disabled"
    if not row["scheduled"]:
        return "unscheduled"
    if str(row["earliest"]).startswith("rt") or str(row["latest"]).startswith("rt"):
        return "realtime"
    return "active"


def _pct(value):
    return f"{round(value * 100)} %"


def _num(value, decimals=0):
    text = f"{value:,.{decimals}f}"
    return text.replace(",", "\0").replace(".", ",").replace("\0", ".")


def noisy_text(stats):
    if stats["noisy"] == "per_day":
        return f"{_num(stats['fired_per_day'], 1)} disparos/día de media"
    if stats["noisy"] == "always":
        return f"Dispara en el {_pct(stats['fire_rate'])} de sus ejecuciones"
    return ""


def dead_text(stats):
    return DEAD_LABELS.get(stats["dead"], "")


def issue_texts(stats):
    attempts = stats["runs"] + stats["skipped"] + stats["errors"]
    texts = []
    for issue in stats["issues"]:
        if issue == "skips":
            texts.append(f"Saltada el {_pct(stats['skipped'] / attempts)} de las veces")
        elif issue == "errors":
            texts.append(f"{_num(stats['errors'])} ejecuciones con error")
        elif issue == "missing_runs":
            texts.append(f"Se ejecuta menos de lo previsto ({_num(stats['attempts_complete'])} de ~{_num(stats['expected'])})")
    if stats["dead"] in SCHEDULER_DEAD:
        texts.insert(0, DEAD_LABELS[stats["dead"]])
    return texts


def trend_text(stats):
    t = stats.get("trend")
    if not t or t["direction"] == "none":
        return ""
    if t["direction"] == "new":
        return "Empieza a disparar"
    if t["direction"] == "flat":
        return "Estable"
    return f"{'▲' if t['change'] > 0 else '▼'} {_pct(abs(t['change']))}"


# --- Análisis -------------------------------------------------------------------------------

def _inventory(service):
    """
    Filas de todas las saved searches de todas las apps y usuarios (las alertas con su configuración de supresión) y sus
    claves.
    """
    rows = []
    for s in all_saved_searches(service):
        row = saved_search_row(s)
        row["is_alert_or_es"] = _is_alert(s) or _is_es_use_case(s)
        c = s.content
        row["suppress"] = _flag(c.get("alert.suppress"))
        row["suppress_period"] = c.get("alert.suppress.period") or ""
        row["digest_mode"] = _flag(c.get("alert.digest_mode", True))
        rows.append(row)
    return rows


def _empty_stats(days):
    return {"runs": [0] * days, "fired": [0] * days, "suppressed": [0] * days, "skipped": [0] * days,
            "errors": [0] * days, "results": 0, "runtime": 0.0, "skip_reasons": set()}


def _as_list(value):
    if value in (None, ""):
        return []
    return value if isinstance(value, list) else [value]


def _int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def analyze_alerts(service, days=7, noisy_per_day=NOISY_PER_DAY):
    """
    Análisis de las alertas del cliente en los últimos 'days' días (hoy incluido). Devuelve los días, los totales diarios,
    un resumen y, por alerta, la fila del listado con 'stats' (ejecuciones, disparos, tendencia, ruidosa, muerta...).
    """
    if days not in ANALYSIS_DAYS:
        raise AnalysisError(f"Periodo no válido: {days} días.")
    today = date.today()
    day_list = [today - timedelta(days=offset) for offset in range(days - 1, -1, -1)]
    day_keys = [d.isoformat() for d in day_list]
    position = {day: i for i, day in enumerate(day_keys)}

    with ThreadPoolExecutor(max_workers=2) as pool:
        inventory_job = pool.submit(_inventory, service)
        scheduler_job = pool.submit(oneshot_results, service, SCHEDULER_SPL.format(offset=days - 1))
        inventory, scheduler = inventory_job.result(), scheduler_job.result()
    if not scheduler and any(r["is_alert_or_es"] and _state(r) == "active" for r in inventory):
        # Sin una sola ejecución registrada todas parecerían muertas: casi siempre es falta de permisos sobre _internal
        raise AnalysisError(f"El log del planificador (index=_internal sourcetype=scheduler) no devuelve datos de los últimos "
                           f"{days} días. Compruebe que el usuario de Splunk de este cliente puede buscar en _internal.")

    # Las filas del planificador se asocian por nombre y app (si hay una privada y otra compartida, a la compartida); si la
    # app no coincide, por nombre si es único
    by_key = {(r["name"], r["app"]): r for r in sorted(inventory, key=lambda r: r["sharing"] != "user")}
    by_name = defaultdict(list)
    for r in inventory:
        by_name[r["name"]].append(r)

    collected = defaultdict(lambda: _empty_stats(days))
    for row in scheduler:
        i = position.get(str(row.get("_time", ""))[:10])
        if i is None:
            continue
        name, app = row.get("savedsearch_name") or "", row.get("app") or ""
        target = by_key.get((name, app)) or (by_name[name][0] if len(by_name.get(name, [])) == 1 else None)
        if not target or not target["is_alert_or_es"]:
            continue
        stats = collected[(target["name"], target["app"], target["owner"])]
        for field in ("runs", "fired", "suppressed", "skipped", "errors"):
            stats[field][i] += _int(row.get(field))
        stats["results"] += _int(row.get("results"))
        try:
            stats["runtime"] += float(row.get("runtime") or 0)
        except (TypeError, ValueError):
            pass
        stats["skip_reasons"].update(_as_list(row.get("skip_reasons")))

    complete_days = day_list[:-1]
    totals = {k: [0] * days for k in ("fired", "runs", "skipped", "errors")}
    alerts = []
    for row in inventory:
        if not row.pop("is_alert_or_es"):
            continue
        raw = collected.get((row["name"], row["app"], row["owner"])) or _empty_stats(days)
        runs, fired = sum(raw["runs"]), sum(raw["fired"])
        state = _state(row)
        stats = {
            "runs": runs, "fired": fired, "suppressed": sum(raw["suppressed"]), "skipped": sum(raw["skipped"]),
            "errors": sum(raw["errors"]), "results": raw["results"],
            "avg_runtime": raw["runtime"] / runs if runs else None,
            "fire_rate": fired / runs if runs else None,
            "fired_per_day": sum(raw["fired"][:-1]) / len(complete_days) if complete_days else fired,
            "expected": expected_runs(row["cron"], complete_days) if state == "active" else None,
            "attempts_complete": sum(sum(raw[k][:-1]) for k in ("runs", "skipped", "errors")),
            "series": raw["fired"], "series_runs": raw["runs"], "series_skipped": raw["skipped"],
            "series_errors": raw["errors"],
            "skip_reasons": sorted(raw["skip_reasons"])[:MAX_SKIP_REASONS],
            "trend": trend(raw["fired"]),
            "state": state, "noisy": None, "dead": None, "issues": [],
        }
        if state == "active":
            stats["noisy"] = noisy_reason(stats, noisy_per_day)
            stats["dead"] = _dead_reason(stats, bool(row["actions"].strip()))
            stats["issues"] = _issues(stats)
        elif state == "unscheduled":
            stats["dead"] = "no_schedule"
        for key in totals:
            for i, value in enumerate(raw[key]):
                totals[key][i] += value
        alerts.append({**row, "stats": stats})

    alerts.sort(key=lambda a: a["name"].lower())
    complete_fired = totals["fired"][:-1]
    states = defaultdict(int)
    for a in alerts:
        states[a["stats"]["state"]] += 1
    return {
        "days": day_keys,
        "period_days": days,
        "rules": {"noisy_per_day": noisy_per_day, "always_rate": ALWAYS_FIRES_RATE, "always_min_runs": ALWAYS_FIRES_MIN_RUNS},
        "totals": totals,
        "summary": {
            "alerts": len(alerts), "active": states["active"], "disabled": states["disabled"],
            "unscheduled": states["unscheduled"], "realtime": states["realtime"],
            "fired": sum(totals["fired"]),
            "fired_avg_day": sum(complete_fired) / len(complete_fired) if complete_fired else 0,
            "skipped": sum(totals["skipped"]), "errors": sum(totals["errors"]),
            "noisy": sum(1 for a in alerts if a["stats"]["noisy"]),
            "dead": sum(1 for a in alerts if a["stats"]["dead"]),
            "scheduler": sum(1 for a in alerts if a["stats"]["issues"] or a["stats"]["dead"] in SCHEDULER_DEAD),
        },
        "alerts": alerts,
    }


def report_lists(result):
    """Ruidosas (por disparos), muertas y con problemas de planificador, con sus textos (informes y CLI)."""
    alerts = result["alerts"]
    item = lambda a, reason: {"name": a["name"], "app": a["app"], "cron": a["cron"], "severity": a["severity"],
                              "suppress": a["suppress"], "reason": reason, "trend": trend_text(a["stats"]), "stats": a["stats"]}
    noisy = sorted((a for a in alerts if a["stats"]["noisy"]), key=lambda a: (-a["stats"]["fired"], a["name"].lower()))
    dead_order = list(DEAD_LABELS)
    dead = sorted((a for a in alerts if a["stats"]["dead"]),
                  key=lambda a: (dead_order.index(a["stats"]["dead"]), a["name"].lower()))
    scheduler = sorted((a for a in alerts if a["stats"]["issues"] or a["stats"]["dead"] in SCHEDULER_DEAD),
                       key=lambda a: (-(a["stats"]["skipped"] + a["stats"]["errors"]), a["name"].lower()))
    return {
        "noisy": [item(a, noisy_text(a["stats"])) for a in noisy],
        "dead": [item(a, dead_text(a["stats"])) for a in dead],
        "scheduler": [item(a, " · ".join(issue_texts(a["stats"]))) for a in scheduler],
    }
