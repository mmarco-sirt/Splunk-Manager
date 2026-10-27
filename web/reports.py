"""
Informes exportables de uno o varios clientes: salud, ingesta, inventario y análisis de alertas, índices y usuarios.

Formatos: Excel (.xlsx), HTML (para ver o imprimir) y PDF (el mismo HTML impreso con Microsoft Edge o Chrome en
modo headless, si están instalados). Se guardan en la carpeta informes/.
"""
import os
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from html import escape
from pathlib import Path

from jinja2 import ChainableUndefined, Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from core.paths import REPORTS_ROOT, reports_dir
from web import services
from web.alert_analysis import NOISY_PER_DAY, analyze_alerts, period_for, report_lists
from web.explorer import INGEST_DAYS, ingest_usage, list_indexes, list_roles, list_saved_searches, list_users
from web.health import LEVEL_LABELS, RANK, check_client, evaluate, monitor
from web.operations import OperationError
from web.services import ClientError
from web.xlsx import Sheet, write_xlsx

SECTIONS = {
    "health": "Salud: conexión, planificador, ingesta de 24 h y fuentes sin datos",
    "ingest": "Ingesta diaria por índice y sourcetype",
    "alerts": "Inventario de alertas",
    "alert_analysis": "Análisis de alertas: ruidosas, muertas y problemas del planificador",
    "indexes": "Índices",
    "users": "Usuarios y roles",
}
REPORT_WORKERS = 4
PDF_TIMEOUT = 120
GB = 1024 ** 3
SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")
REPORT_FILE = re.compile(r"^[A-Za-z0-9_.-]+\.(xlsx|html|pdf)$")
TEMPLATES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
BROWSER_PATHS = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)


def find_browser():
    """Navegador Chromium para convertir el HTML en PDF (Edge viene con Windows), o None."""
    for path in BROWSER_PATHS:
        if os.path.isfile(path):
            return path
    for name in ("msedge", "chrome", "chromium", "google-chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def formats():
    result = {"xlsx": "Excel (.xlsx)", "html": "HTML (ver o imprimir)"}
    if find_browser():
        result["pdf"] = "PDF"
    return result


# --- Formato ------------------------------------------------------------------------

def fmt_num(value, decimals=0):
    if value is None:
        return "—"
    text = f"{value:,.{decimals}f}"
    return text.replace(",", "\0").replace(".", ",").replace("\0", ".")


def fmt_bytes(value):
    if value is None:
        return "—"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while value >= 1024 and i < len(units) - 1:
        value /= 1024
        i += 1
    return f"{fmt_num(value, 1 if i else 0)} {units[i]}"


def fmt_gb(value):
    """Entitlement en GB como en la Cloud Monitoring Console; None es N/A."""
    return "N/A" if not value else f"{fmt_num(value, 0 if value == int(value) else 1)} GB"


def fmt_pct(value):
    return "—" if value is None else f"{fmt_num(value * 100, 0)} %"


def fmt_time(epoch):
    return datetime.fromtimestamp(epoch).strftime("%d/%m/%Y %H:%M") if epoch else "—"


def fmt_day(iso):
    """'2026-09-25' -> '25/09/2026'"""
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%d/%m/%Y")
    except (TypeError, ValueError):
        return iso or "—"


def fmt_change(change):
    if change is None:
        return ""
    return f"{'▲' if change >= 0 else '▼'} {fmt_pct(abs(change))}"


# --- Recogida de datos ----------------------------------------------------------------

def _alert_kind(row):
    return "ES" if row["is_es"] else "CU-" if row["is_cu"] else "Otra"


def collect_client(name, sections, days, threshold, ignored):
    """Datos de un cliente para el informe. Los fallos de cada sección se guardan en 'errors' sin parar el resto."""
    client = services.find_client(name)
    data = {"name": name, "host": client.get("host", ""), "errors": {}, "error": None, "info": None}
    if "health" in sections:
        result = check_client(name)
        monitor.store(name, result)
        data["health"] = result
        data["diagnosis"] = evaluate(result, threshold, ignored)
        if result.get("status") == "error":
            data["error"] = result.get("error")
            return data
        data["info"] = result.get("info")
    try:
        service = services.get_service(name)
    except ClientError as e:
        data["error"] = str(e)
        return data
    data["info"] = data["info"] or services.server_info(name)

    collectors = {
        "ingest": lambda: ingest_usage(service, days),
        "alerts": lambda: sorted((r for r in list_saved_searches(service) if r["is_alert"] or r["is_es"]),
                                 key=lambda r: r["name"].lower()),
        "alert_analysis": lambda: _alert_analysis(service, days),
        "indexes": lambda: list_indexes(service),
        "users": lambda: {"users": list_users(service), "roles": list_roles(service)},
    }
    for key, collect in collectors.items():
        if key in sections:
            try:
                data[key] = collect()
            except Exception as e:
                data["errors"][key] = f"{type(e).__name__}: {e}"
    return data


def _alert_analysis(service, days):
    """Análisis de alertas para el informe (como mucho 30 días: lo que suele guardar _internal)."""
    result = analyze_alerts(service, period_for(days))
    return {"period_days": result["period_days"], "summary": result["summary"], **report_lists(result)}


def _client_summary(c, threshold):
    """Fila resumen de un cliente (común a Excel y HTML)."""
    checks = (c.get("health") or {}).get("checks") or {}
    data = lambda key: checks.get(key, {}).get("data") if checks.get(key, {}).get("ok") else None
    alerts, scheduler, ingest = data("alerts"), data("scheduler"), data("ingest")
    diagnosis = c.get("diagnosis") or {}
    period = c.get("ingest")
    entitlements = (period or {}).get("license") or {}
    quota = entitlements["ingest"] * GB if entitlements.get("ingest") else None
    return {
        "level": diagnosis.get("level") or ("critical" if c.get("error") else "pending"),
        "problems": diagnosis.get("problems") or ([{"level": "critical", "text": c["error"]}] if c.get("error") else []),
        "silent": diagnosis.get("silent") or [],
        "version": (c.get("info") or {}).get("version"),
        "server": (c.get("info") or {}).get("serverName"),
        "alerts": alerts, "scheduler": scheduler, "ingest24": ingest,
        "avg_day": period["avg_day"] if period else None,
        "peak": period["peak"] if period else None,
        "license": entitlements,
        "license_use": period["avg_day"] / quota if quota else None,
    }


# --- Excel ---------------------------------------------------------------------------------

def write_excel(path, report):
    clients, sections, threshold = report["clients"], report["sections"], report["threshold"]
    summaries = {c["name"]: _client_summary(c, threshold) for c in clients}
    has_health = "health" in sections
    has_ingest = "ingest" in sections

    columns = [("Cliente", "text"), ("Host", "text"), ("Versión", "text")]
    if has_health:
        columns += [("Estado", "text"), ("Diagnóstico", "wrap"), ("Alertas activas", "int"), ("Alertas totales", "int"),
                    ("CU- deshabilitados", "int"), ("Casos de uso ES", "int"), ("Planificador OK 24 h", "int"),
                    ("Saltadas 24 h", "int"), ("Con error 24 h", "int"), ("Ingesta 24 h (GB)", "decimal"),
                    ("Cambio vs media 7 d", "percent"), (f"Fuentes sin datos > {threshold} h", "int")]
    if has_ingest:
        columns += [(f"Media diaria {report['days']} d (GB)", "decimal"), ("Día pico", "text"), ("Pico (GB)", "decimal"),
                    ("Licencia de ingesta (GB/día)", "decimal"), ("Uso medio de licencia", "percent"),
                    ("Días por encima de licencia", "int"), ("Almacenamiento DDAS (GB)", "decimal"),
                    ("Archivo DDAA (GB)", "decimal"), ("Restauración DDAA (GB)", "decimal")]
    has_analysis = "alert_analysis" in sections
    if has_analysis:
        columns += [("Disparos de alertas", "int"), ("Alertas ruidosas", "int"), ("Alertas muertas", "int"),
                    ("Alertas con problemas de planificador", "int")]
    columns += [("Error", "wrap")]

    rows = []
    for c in clients:
        s = summaries[c["name"]]
        row = [c["name"], c["host"], s["version"]]
        if has_health:
            a, sch, i = s["alerts"] or {}, s["scheduler"] or {}, s["ingest24"] or {}
            row += [LEVEL_LABELS[s["level"]], "\n".join(p["text"] for p in s["problems"]),
                    (a["total"] - a["disabled"]) if a else None, a.get("total"), a.get("cu_disabled"), a.get("es"),
                    sch.get("success"), sch.get("skipped"), sch.get("errors"),
                    i["last24h_bytes"] / GB if i else None, i.get("change"),
                    len(s["silent"]) if c.get("health", {}).get("checks", {}).get("sources", {}).get("ok") else None]
        if has_ingest:
            row += [s["avg_day"] / GB if s["avg_day"] is not None else None,
                    s["peak"]["day"] if s["peak"] else None, s["peak"]["bytes"] / GB if s["peak"] else None]
            lic = s["license"]
            row += [lic.get("ingest"), s["license_use"], lic.get("over_days"), lic.get("ddas"), lic.get("ddaa"), lic.get("restore")]
        if has_analysis:
            summary = (c.get("alert_analysis") or {}).get("summary") or {}
            row += [summary.get("fired"), summary.get("noisy"), summary.get("dead"), summary.get("scheduler")]
        row += ["\n".join([c["error"] or ""] + [f"{k}: {v}" for k, v in c["errors"].items()]).strip() or None]
        rows.append(row)
    sheets = [Sheet("Resumen", columns, rows)]

    if has_health:
        sheets.append(Sheet("Problemas", [("Cliente", "text"), ("Nivel", "text"), ("Problema", "wrap")],
                            [[c["name"], LEVEL_LABELS[p["level"]], p["text"]] for c in clients for p in summaries[c["name"]]["problems"]]))
        now = report["generated"].timestamp()
        sheets.append(Sheet("Fuentes sin datos",
                            [("Cliente", "text"), ("Índice", "text"), ("Sourcetype", "text"), ("Último evento", "text"),
                             ("Horas sin datos", "decimal"), ("Eventos 7 d", "int")],
                            [[c["name"], src["index"], src["sourcetype"], fmt_time(src["last"]), (now - src["last"]) / 3600, src["count"]]
                             for c in clients for src in summaries[c["name"]]["silent"]]))
        sheets.append(Sheet("Planificador 24 h",
                            [("Cliente", "text"), ("Búsqueda", "text"), ("App", "text"), ("Estado", "text"), ("Motivo", "wrap"), ("Veces", "int")],
                            [[c["name"], p["name"], p["app"], p["status"], p["reason"], p["count"]]
                             for c in clients for p in (summaries[c["name"]]["scheduler"] or {}).get("problems", [])]))

    if has_ingest:
        with_ingest = [c for c in clients if c.get("ingest")]
        if with_ingest:
            days = with_ingest[0]["ingest"]["days"]
            daily = [[day] + [c["ingest"]["totals"][i] / GB for c in with_ingest] + [sum(c["ingest"]["totals"][i] for c in with_ingest) / GB]
                     for i, day in enumerate(days)]
            sheets.append(Sheet("Ingesta diaria (GB)", [("Día", "text")] + [(c["name"], "decimal") for c in with_ingest] + [("Total", "decimal")], daily))
        sheets.append(Sheet("Ingesta por índice",
                            [("Cliente", "text"), ("Índice", "text"), ("Media diaria (GB)", "decimal"), ("Último día (GB)", "decimal"),
                             ("Total periodo (GB)", "decimal"), ("Peso", "percent")],
                            [[c["name"], r["name"], r["avg_day"] / GB, r["last_day"] / GB, r["total"] / GB,
                              r["total"] / c["ingest"]["total"] if c["ingest"]["total"] else None]
                             for c in with_ingest for r in c["ingest"]["indexes"]]))
        sheets.append(Sheet("Ingesta por sourcetype",
                            [("Cliente", "text"), ("Sourcetype", "text"), ("Índices", "text"), ("Media diaria (GB)", "decimal"),
                             ("Total periodo (GB)", "decimal")],
                            [[c["name"], r["name"], ", ".join(r.get("indexes") or []), r["avg_day"] / GB, r["total"] / GB]
                             for c in with_ingest for r in c["ingest"]["sourcetypes"]]))

    if "alerts" in sections:
        sheets.append(Sheet("Alertas",
                            [("Cliente", "text"), ("Nombre", "text"), ("App", "text"), ("Propietario", "text"), ("Estado", "text"),
                             ("Tipo", "text"), ("Programación", "text"), ("Severidad", "text"), ("Acciones", "text"),
                             ("Etiqueta ES", "text"), ("Descripción", "wrap")],
                            [[c["name"], r["name"], r["app"], r["owner"], "Deshabilitada" if r["disabled"] else "Habilitada",
                              _alert_kind(r), r["cron"] if r["scheduled"] else "No programada", r["severity"], r["actions"],
                              r["es_label"], r["description"]]
                             for c in clients for r in c.get("alerts") or []]))

    if has_analysis:
        analyzed = [c for c in clients if c.get("alert_analysis")]
        trend_change = lambda st: st["trend"]["change"] if st.get("trend") and st["trend"]["change"] is not None else None
        sheets.append(Sheet("Alertas ruidosas",
                            [("Cliente", "text"), ("Alerta", "text"), ("App", "text"), ("Motivo", "text"), ("Disparos", "int"),
                             ("Media diaria", "decimal"), ("Resultados", "int"), ("Ejecuciones con disparo", "percent"),
                             ("Tendencia", "percent"), ("Supresión", "text"), ("Severidad", "text")],
                            [[c["name"], a["name"], a["app"], a["reason"], a["stats"]["fired"], a["stats"]["fired_per_day"],
                              a["stats"]["results"], a["stats"]["fire_rate"], trend_change(a["stats"]),
                              "Sí" if a["suppress"] else "No", a["severity"]]
                             for c in analyzed for a in c["alert_analysis"]["noisy"]]))
        sheets.append(Sheet("Alertas muertas",
                            [("Cliente", "text"), ("Alerta", "text"), ("App", "text"), ("Motivo", "text"), ("Programación", "text"),
                             ("Ejecuciones", "int"), ("Esperadas", "int"), ("Saltadas", "int"), ("Con error", "int")],
                            [[c["name"], a["name"], a["app"], a["reason"], a["cron"] or "No programada", a["stats"]["runs"],
                              a["stats"]["expected"], a["stats"]["skipped"], a["stats"]["errors"]]
                             for c in analyzed for a in c["alert_analysis"]["dead"]]))
        sheets.append(Sheet("Alertas y planificador",
                            [("Cliente", "text"), ("Alerta", "text"), ("App", "text"), ("Problema", "wrap"), ("Ejecutadas", "int"),
                             ("Saltadas", "int"), ("Con error", "int"), ("Esperadas", "int"), ("Motivo del salto", "wrap"),
                             ("Duración media (s)", "decimal")],
                            [[c["name"], a["name"], a["app"], a["reason"], a["stats"]["runs"], a["stats"]["skipped"],
                              a["stats"]["errors"], a["stats"]["expected"], "\n".join(a["stats"]["skip_reasons"]),
                              a["stats"]["avg_runtime"]]
                             for c in analyzed for a in c["alert_analysis"]["scheduler"]]))

    if "indexes" in sections:
        sheets.append(Sheet("Índices",
                            [("Cliente", "text"), ("Índice", "text"), ("Tipo", "text"), ("Eventos", "int"), ("Tamaño (MB)", "decimal"),
                             ("Máximo (MB)", "decimal"), ("Retención (días)", "decimal"), ("Archivado (días)", "int"),
                             ("Deshabilitado", "text"), ("Último evento", "text")],
                            [[c["name"], r["name"], r["datatype"], r["events"], r["size_mb"], r["max_size_mb"] or None,
                              r["retention_days"], r["archive_days"] or None, "Sí" if r["disabled"] else "No", fmt_time(r["max_time"])]
                             for c in clients for r in c.get("indexes") or []]))

    if "users" in sections:
        sheets.append(Sheet("Usuarios",
                            [("Cliente", "text"), ("Usuario", "text"), ("Nombre", "text"), ("Email", "text"), ("Roles", "text"),
                             ("Tipo", "text"), ("Bloqueado", "text"), ("Último login", "text")],
                            [[c["name"], u["name"], u["realname"], u["email"], ", ".join(u["roles"]), u["type"],
                              "Sí" if u["locked"] else "No", fmt_time(_epoch(u["last_login"]))]
                             for c in clients for u in (c.get("users") or {}).get("users", [])]))
        sheets.append(Sheet("Roles",
                            [("Cliente", "text"), ("Rol", "text"), ("Hereda de", "text"), ("Índices permitidos", "wrap"),
                             ("Capacidades propias", "int")],
                            [[c["name"], r["name"], ", ".join(r["imported_roles"]), ", ".join(r["indexes_allowed"]), len(r["capabilities"])]
                             for c in clients for r in (c.get("users") or {}).get("roles", [])]))

    write_xlsx(path, sheets, title=report["title"])


def _epoch(value):
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


# --- HTML y PDF ------------------------------------------------------------------------------

def columns_svg(days, values, width=680, height=150):
    """Gráfico de columnas en SVG (ingesta diaria) con la media de los días completos."""
    if not values:
        return Markup("")
    top = max(max(values), 1)
    complete = values[:-1]
    average = sum(complete) / len(complete) if complete else 0
    plot_h, left, bottom = height - 24, 54, height - 18
    step = (width - left) / len(values)
    bar_w = max(step * 0.72, 1)
    parts = [f'<svg class="chart" viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="Ingesta diaria">']
    for frac in (0.5, 1):
        y = bottom - plot_h * frac
        parts.append(f'<line x1="{left}" x2="{width}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>'
                     f'<text x="{left - 6}" y="{y + 3:.1f}" class="tick" text-anchor="end">{escape(fmt_bytes(top * frac))}</text>')
    parts.append(f'<line x1="{left}" x2="{width}" y1="{bottom}" y2="{bottom}" class="axis"/>')
    for i, value in enumerate(values):
        h = plot_h * value / top
        x = left + i * step + (step - bar_w) / 2
        cls = "bar partial" if i == len(values) - 1 else "bar"
        parts.append(f'<rect x="{x:.1f}" y="{bottom - h:.1f}" width="{bar_w:.1f}" height="{max(h, 0):.1f}" class="{cls}">'
                     f'<title>{escape(days[i])}: {escape(fmt_bytes(value))}</title></rect>')
    if average:
        y = bottom - plot_h * average / top
        parts.append(f'<line x1="{left}" x2="{width}" y1="{y:.1f}" y2="{y:.1f}" class="avg"/>'
                     f'<text x="{left + 4}" y="{y - 4:.1f}" class="tick avg-label">media {escape(fmt_bytes(average))}</text>')
    for i in (0, len(values) // 2, len(values) - 1):
        x = left + i * step + step / 2
        label = datetime.strptime(days[i], "%Y-%m-%d").strftime("%d/%m")
        parts.append(f'<text x="{x:.1f}" y="{height - 4}" class="tick" text-anchor="middle">{label}</text>')
    parts.append("</svg>")
    return Markup("".join(parts))


def _environment():
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]),
                      undefined=ChainableUndefined)
    env.filters.update(num=fmt_num, bytes=fmt_bytes, pct=fmt_pct, time=fmt_time, change=fmt_change, day=fmt_day, gb=fmt_gb)
    env.globals.update(LEVEL_LABELS=LEVEL_LABELS, columns_svg=columns_svg, alert_kind=_alert_kind, epoch=_epoch,
                       NOISY_PER_DAY=NOISY_PER_DAY, GB=GB)
    return env


def render_html(report):
    clients = report["clients"]
    summaries = {c["name"]: _client_summary(c, report["threshold"]) for c in clients}
    totals = {level: sum(1 for s in summaries.values() if s["level"] == level) for level in LEVEL_LABELS}
    ingest24 = [s["ingest24"]["last24h_bytes"] for s in summaries.values() if s["ingest24"]]
    return _environment().get_template("report.html").render(
        report=report, clients=clients, summaries=summaries, sections=report["sections"], totals=totals,
        ingest24_total=sum(ingest24) if ingest24 else None, rank=RANK)


def write_html(path, report):
    with open(path, "w", encoding="utf-8") as f:
        f.write(render_html(report))


def write_pdf(path, report):
    browser = find_browser()
    if not browser:
        raise RuntimeError("No se encontró Microsoft Edge ni Chrome para generar el PDF.")
    work = tempfile.mkdtemp(prefix="splunk_manager_pdf_")
    try:
        html_path = os.path.join(work, "informe.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(render_html({**report, "pdf": True}))
        command = [browser, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                   "--disable-extensions", f"--user-data-dir={os.path.join(work, 'perfil')}",
                   "--no-pdf-header-footer", "--print-to-pdf-no-header", f"--print-to-pdf={path}",
                   Path(html_path).as_uri()]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.run(command, timeout=PDF_TIMEOUT, capture_output=True, creationflags=flags)
        if not os.path.isfile(path) or os.path.getsize(path) == 0:
            raise RuntimeError("El navegador no generó el PDF.")
    finally:
        shutil.rmtree(work, ignore_errors=True)


WRITERS = {"xlsx": write_excel, "html": write_html, "pdf": write_pdf}


# --- Tarea de generación ----------------------------------------------------------------------------

def build_report(clients, sections, output_formats, options):
    """Valida la petición y devuelve (título, función) para generar el informe en una tarea."""
    clients = [c for c in dict.fromkeys(clients or []) if c]
    sections = [s for s in SECTIONS if s in (sections or [])]
    available = formats()
    output_formats = [f for f in available if f in (output_formats or [])]
    if not clients:
        raise OperationError("Seleccione al menos un cliente.")
    if not sections:
        raise OperationError("Seleccione al menos una sección del informe.")
    if not output_formats:
        raise OperationError("Seleccione al menos un formato.")
    days = int(options.get("days") or 30)
    if days not in INGEST_DAYS:
        raise OperationError("Periodo de ingesta no válido.")
    threshold = int(options.get("threshold") or 24)
    if not 1 <= threshold <= 24 * 30:
        raise OperationError("Umbral de fuentes sin datos no válido.")
    ignored = [str(k) for k in options.get("ignored") or []]

    def run():
        generated = datetime.now()
        print(f"▶ Recopilando datos de {len(clients)} {'cliente' if len(clients) == 1 else 'clientes'}…")
        results = {}
        with ThreadPoolExecutor(max_workers=min(REPORT_WORKERS, len(clients))) as pool:
            futures = {pool.submit(collect_client, name, sections, days, threshold, ignored): name for name in clients}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    data = future.result()
                except Exception as e:
                    data = {"name": name, "host": "", "errors": {}, "error": f"{type(e).__name__}: {e}", "info": None}
                results[name] = data
                if data.get("error"):
                    message = data["error"]
                    print(f"❌ {message}" if message.startswith(f"{name}:") else f"❌ {name}: {message}")
                else:
                    failed = ", ".join(data["errors"]) if data["errors"] else ""
                    print(f"  · {name}: datos recopilados" + (f" (⚠️ sin {failed})" if failed else ""))

        scope = SAFE_NAME.sub("_", clients[0]).strip("_") if len(clients) == 1 else f"{len(clients)}_clientes"
        base = f"informe_{generated:%Y%m%d-%H%M%S}_{scope or 'cliente'}"
        report = {
            "title": f"Informe de {clients[0]}" if len(clients) == 1 else f"Informe de {len(clients)} clientes",
            "generated": generated, "clients": [results[n] for n in clients], "sections": sections,
            "days": days, "threshold": threshold,
        }
        folder = reports_dir()
        for fmt in output_formats:
            path = os.path.join(folder, f"{base}.{fmt}")
            try:
                WRITERS[fmt](path, report)
                print(f"✅ Informe {fmt.upper()} generado: informes/{os.path.basename(path)}")
            except Exception as e:
                print(f"❌ No se pudo generar el informe {fmt.upper()}: {e}")

    title = f"Informe de {clients[0]}" if len(clients) == 1 else f"Informe de {len(clients)} clientes"
    return title, run


# --- Archivos generados -------------------------------------------------------------------------

def list_reports():
    if not os.path.isdir(REPORTS_ROOT):
        return []
    files = []
    for name in os.listdir(REPORTS_ROOT):
        path = os.path.join(REPORTS_ROOT, name)
        if REPORT_FILE.match(name) and os.path.isfile(path):
            stat = os.stat(path)
            files.append({"name": name, "size": stat.st_size, "mtime": stat.st_mtime, "format": name.rsplit(".", 1)[1]})
    return sorted(files, key=lambda f: f["mtime"], reverse=True)


def report_path(name):
    """Ruta segura a un informe de informes/; impide salir de la carpeta."""
    if not name or name != os.path.basename(name) or not REPORT_FILE.match(name):
        raise OperationError("Nombre de informe no válido.")
    path = os.path.join(REPORTS_ROOT, name)
    if not os.path.isfile(path):
        raise OperationError(f"No existe el informe {name}.")
    return path
