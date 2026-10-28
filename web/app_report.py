"""
Informe de validación de una versión de la app en Excel, HTML y PDF: datos del paquete, problemas (del paquete y de la
validación contra un cliente), cambios respecto a otra versión o a un cliente y resultado de la ejecución de búsquedas.
Se guarda en informes/ como el resto de informes.
"""
import os
import re
from datetime import datetime

from core.paths import reports_dir
from web import app_packages, services
from web.app_diff import client_snapshot, diff_client, diff_packages, type_label
from web.app_validation import CATEGORIES, counts, validate_package
from web.operations import OperationError
from web.reports import _environment, fmt_time, formats, html_to_pdf
from web.xlsx import Sheet, write_xlsx

LEVELS = {"error": "Error", "warning": "Aviso", "info": "Info"}
CHANGES = {"added": ("Nuevo", "good"), "removed": ("Eliminado", "bad"), "modified": ("Modificado", "warn"),
           "affected": ("Usa macros que cambian", "warn"), "present": ("En los dos", "muted"), "same": ("Igual", "muted")}
RUNS = {"ok": ("OK", "good"), "warning": ("Con avisos", "warn"), "error": ("Error", "bad"), "blocked": ("No ejecutada", "muted"),
        "timeout": ("Tiempo agotado", "warn"), "cancelled": ("Cancelada", "muted")}
LEVEL_ORDER = {"error": 0, "warning": 1, "info": 2}
SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")


def _latest(package_id, kind, wanted):
    """Resultado guardado: el indicado, el último ('latest') o ninguno."""
    if not wanted:
        return None
    if wanted == "latest":
        found = app_packages.list_results(package_id, kind)
        return app_packages.read_result(package_id, kind, found[0]["id"]) if found else None
    return app_packages.read_result(package_id, kind, wanted)


def collect(package_id, base=None, check="latest", run="latest"):
    """Datos del informe: paquete, problemas, cambios respecto a 'base' y la validación y ejecución elegidas."""
    package = app_packages.load_package(package_id)
    meta = app_packages.get_meta(package_id)
    info = package.info()
    check_data = _latest(package_id, "checks", check)
    run_data = _latest(package_id, "runs", run)
    issues = validate_package(package) + (check_data or {}).get("issues", [])
    issues.sort(key=lambda i: (LEVEL_ORDER.get(i["level"], 3), i["category"], i.get("object") or "", i.get("file") or ""))
    diff, base_label = None, ""
    if base and base.get("package"):
        other = app_packages.load_package(base["package"])
        diff = diff_packages(package, other)
        diff["base"] = {"kind": "package", "id": base["package"], "version": other.info()["version"]}
        base_label = f"la versión {other.info()['version']} ({app_packages.get_meta(base['package'])['filename']})"
    elif base and base.get("client"):
        service = services.get_service(base["client"])
        snapshot = client_snapshot(service, info["id"], package.conf_names())
        diff = diff_client(package, snapshot)
        diff["base"] = {"kind": "client", "client": base["client"], "installed": snapshot["installed"]}
        installed = snapshot["installed"]
        base_label = f"{base['client']} ({'versión ' + installed['version'] if installed else 'no instalada'})"
    return {
        "title": f"Validación de {info['label'] or info['id']} {info['version']}", "generated": datetime.now(),
        "app": info, "meta": meta, "issues": issues, "counts": counts(issues), "check": check_data, "run": run_data,
        "diff": diff, "base_label": base_label,
    }


# --- Escritura ----------------------------------------------------------------------------------

def render_html(report):
    env = _environment()
    env.globals.update(LEVELS=LEVELS, CATEGORIES=CATEGORIES, CHANGES=CHANGES, RUNS=RUNS, TYPE_LABELS=type_label)
    return env.get_template("app_validation.html").render(report=report)


def write_html(path, report):
    html = render_html(report)   # antes de abrir el archivo, para no dejarlo vacío si falla
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


def write_pdf(path, report):
    html_to_pdf(render_html({**report, "pdf": True}), path)


def write_excel(path, report):
    app, meta = report["app"], report["meta"]
    summary = [
        ["App", app["label"] or app["id"]], ["Id", app["id"]], ["Versión", app["version"]], ["Build", app["build"] or ""],
        ["Archivo", meta["filename"]], ["SHA-256", meta["sha256"]], ["Subido", fmt_time(meta["uploaded"])],
        ["Errores", report["counts"]["error"]], ["Avisos", report["counts"]["warning"]], ["Info", report["counts"]["info"]],
        ["Comparado con", report["base_label"] or "—"],
        ["Validación en cliente", f"{report['check']['client']} · {fmt_time(report['check']['created'])}" if report["check"] else "—"],
        ["Ejecución", f"{report['run']['client']} · {fmt_time(report['run']['created'])}" if report["run"] else "—"],
    ]
    sheets = [
        Sheet("Resumen", [("Dato", "text"), ("Valor", "wrap")], [[k, str(v)] for k, v in summary]),
        Sheet("Validación", [("Nivel", "text"), ("Origen", "text"), ("Categoría", "text"), ("Objeto", "text"),
                             ("Archivo", "text"), ("Línea", "int"), ("Problema", "wrap")],
              [[LEVELS.get(i["level"], i["level"]), i.get("source") or "", CATEGORIES.get(i["category"], i["category"]),
                i.get("object") or "", i.get("file") or "", i.get("line"), i["message"]] for i in report["issues"]]),
    ]
    if report["diff"]:
        items = report["diff"]["items"]
        sheets.append(Sheet("Cambios", [("Tipo", "text"), ("Objeto", "text"), ("Estado", "text"), ("Cambia el SPL", "text"),
                                        ("Macros que cambian y usa", "wrap"), ("Campos", "int")],
                            [[type_label(i["type"]), i["name"], CHANGES[i["status"]][0],
                              ("Sí" if i.get("spl_changed") else "No") if i["type"] == "savedsearches" else "",
                              ", ".join(i.get("affected_by") or []), len(i.get("fields") or [])] for i in items]))
        sheets.append(Sheet("Cambios campo a campo", [("Tipo", "text"), ("Objeto", "text"), ("Campo", "text"),
                                                      ("Antes", "wrap"), ("Después", "wrap")],
                            [[type_label(i["type"]), i["name"], f["field"], f["before"] if f["before"] is not None else "(no existía)",
                              f["after"] if f["after"] is not None else "(se elimina)"]
                             for i in items for f in i.get("fields") or []]))
    if report["run"]:
        sheets.append(Sheet("Ejecución", [("Búsqueda", "text"), ("Estado", "text"), ("Resultados", "int"), ("Antes", "int"),
                                          ("Diferencia", "int"), ("Duración (s)", "decimal"), ("Desde", "text"), ("Hasta", "text"),
                                          ("Mensaje", "wrap")],
                            [[r["name"], RUNS.get(r["status"], (r["status"],))[0], r["result_count"],
                              (r["previous"] or {}).get("result_count"),
                              r["result_count"] - r["previous"]["result_count"]
                              if r.get("previous") and r["result_count"] is not None and r["previous"].get("result_count") is not None else None,
                              r["duration"], r["earliest"], r["latest"], r["message"]] for r in report["run"]["results"]]))
    write_xlsx(path, sheets, title=report["title"])


WRITERS = {"xlsx": write_excel, "html": write_html, "pdf": write_pdf}


def build_report(package_id, output_formats, options):
    """(título, función) de la tarea que genera el informe de validación en los formatos pedidos."""
    meta = app_packages.get_meta(package_id)
    available = formats()
    output_formats = [f for f in available if f in (output_formats or [])]
    if not output_formats:
        raise OperationError("Seleccione al menos un formato.")
    options = options or {}
    base = options.get("base") if isinstance(options.get("base"), dict) else None

    def run():
        print(f"▶ Recopilando los datos de {meta['app']} {meta['version']}…")
        report = collect(package_id, base, options.get("check") or None, options.get("run") or None)
        stamp = report["generated"].strftime("%Y%m%d-%H%M%S")
        name = SAFE_NAME.sub("_", f"validacion_{meta['app']}_{meta['version']}_{stamp}").strip("_")
        for fmt in output_formats:
            path = os.path.join(reports_dir(), f"{name}.{fmt}")
            try:
                WRITERS[fmt](path, report)
                print(f"✅ Informe {fmt.upper()} generado: informes/{os.path.basename(path)}")
            except Exception as e:
                print(f"❌ No se pudo generar el informe {fmt.upper()}: {e}")

    return f"Informe de validación de {meta['app']} {meta['version']}", run
