"""
Validación de una versión de la app antes de desplegarla.

1. Paquete (sin Splunk): codificación de los archivos, sintaxis de los .conf, app.conf, local/ en el paquete, estándar de
   nombres de las saved searches, campos obligatorios de las alertas, versiones TEST habilitadas o sin supresión, macros y
   lookups que no están en el paquete, CSV de los lookups y XML de los dashboards.
2. Contra un cliente (tarea): versión instalada, macros y lookups que no existen ni en el paquete ni en el cliente (o no
   se ven desde la app) y el SPL de cada búsqueda pasado por el parser de Splunk, sin ejecutarlo.
3. Ejecución (tarea): el SPL de las búsquedas elegidas se lanza como búsqueda ad hoc (nunca se lanza la saved search, así
   que no se disparan sus acciones) con las macros del paquete ya sustituidas, y opcionalmente también la versión anterior
   para comparar el nº de resultados. No se ejecuta nada que escriba o envíe datos (outputlookup, collect, sendemail…),
   no se crea ni modifica nada en Splunk y los jobs de búsqueda se borran al terminar.
"""
import csv
import io
import json
import re
import time
from xml.etree import ElementTree

from splunklib.binding import HTTPError

from core.conventions import is_cu_alert, is_test_alert, strip_test_prefix
from core.dependencies import PROBLEMS, resolve_dependencies
from core.spl import blocked_commands, commands, expand_macros
from core.splunk_conf import effective_stanza, is_text_path
from web import app_packages, services
from web.alert_analysis import expected_runs
from web.app_diff import installed_app
from web.dependencies import macro_records
from web.explorer import _job_messages, _read_json
from web.jobs import cancel_requested, current_job, set_progress
from web.lookups import lookup_records

CATEGORIES = {
    "encoding": "Codificación", "syntax": "Sintaxis .conf", "app": "App", "naming": "Nombres", "fields": "Campos obligatorios",
    "test": "Versión TEST", "dependencies": "Dependencias", "lookups": "Lookups", "views": "Dashboards", "spl": "SPL",
}
CU_NAME = re.compile(r"^(?:TEST - )?CU-([A-Z0-9]+(?:-[A-Z0-9]+)*)-(\d+)-(T\d{4}(?:\.\d{3})?) (\S.*)$")
MITRE = re.compile(r"^T\d{4}(?:\.\d{3})?$")
VERSION = re.compile(r"^\d+\.\d+\.\d+([.-][0-9A-Za-z.-]+)?$")
LOOKUP_KEYS = ("filename", "collection", "external_cmd")
DEFAULT_EARLIEST = "-24h"
DEFAULT_TIMEOUT = 300
MAX_TIMEOUT = 3600
POLL_SECONDS = 1.0
JOB_TTL = 600            # segundos que Splunk guarda el job si no se pudiera borrar
MAX_PARSER_URL = 6000    # el parser se consulta por GET: SPL más largo no se le envía


def _flag(value):
    return str(value or "").strip().lower() in ("1", "true", "t", "yes", "y", "on")


def _issue(level, category, message, obj="", file="", line=None, source="paquete"):
    return {"level": level, "category": category, "object": obj, "file": file, "line": line, "message": message,
            "source": source}


def counts(issues):
    return {level: sum(1 for i in issues if i["level"] == level) for level in ("error", "warning", "info")}


# --- Reglas de las saved searches ------------------------------------------------------------

def name_problems(name):
    """Qué no cumple un nombre del estándar «[TEST - ]CU-<TEC>-<ID>-<MITRE> <Nombre>» ([] si lo cumple)."""
    if CU_NAME.match(name):
        return []
    problems = []
    if is_test_alert(name) and not name.startswith("TEST - CU-"):
        problems.append(f"El prefijo de pruebas debe ser exactamente «TEST - » (está escrito «{name[:name.find('CU-')]}»).")
    base = strip_test_prefix(name)
    if not base.startswith("CU-"):
        return problems + ["No sigue el estándar «CU-<TEC>-<ID>-<MITRE> <Nombre>» (o «TEST - CU-…» en pruebas)."]
    code, _, title = base[3:].partition(" ")
    parts = code.split("-")
    if len(parts) < 3:
        return problems + ["Le faltan partes: debe ser «CU-<TEC>-<ID>-<MITRE> <Nombre>», p. ej. CU-CA-AUTH-1152-T1078 Nombre."]
    tec, ident, mitre = parts[:-2], parts[-2], parts[-1]
    if not all(re.fullmatch(r"[A-Z0-9]+", t) for t in tec):
        problems.append(f"La tecnología «{'-'.join(tec)}» debe ir en mayúsculas (letras y números, separados por guiones).")
    if not ident.isdigit():
        problems.append(f"El ID «{ident}» debe ser numérico.")
    if not MITRE.match(mitre):
        problems.append(f"La técnica MITRE «{mitre}» no tiene el formato T1234 o T1234.567.")
    if not title.strip():
        problems.append("Falta el nombre descriptivo después de la técnica MITRE.")
    return problems or ["No sigue el estándar «CU-<TEC>-<ID>-<MITRE> <Nombre>»."]


def alert_actions(fields):
    """Acciones de una alerta: las de 'actions' y las action.<nombre> = 1."""
    listed = {a.strip() for a in str(fields.get("actions") or "").split(",") if a.strip()}
    enabled = {k[len("action."):] for k, v in fields.items() if re.fullmatch(r"action\.[^.]+", k) and _flag(v)}
    return sorted(listed | enabled)


def is_alert(name, fields):
    """
    Una saved search es una alerta si sigue el nombre de los casos de uso o tiene condición de disparo. Un informe
    programado que solo envía un email no lo es.
    """
    return is_cu_alert(name) or bool(fields.get("counttype") or fields.get("alert_condition")) or _flag(fields.get("alert.track"))


def alert_problems(name, fields):
    """[(nivel, categoría, mensaje)] de los campos obligatorios de una alerta y de su versión TEST."""
    found = []
    missing = []
    if not str(fields.get("description") or "").strip():
        missing.append("descripción (description)")
    cron = str(fields.get("cron_schedule") or "").strip()
    if not cron:
        missing.append("programación (cron_schedule)")
    elif expected_runs(cron, []) is None:
        found.append(("error", "fields", f"El cron «{cron}» no es válido."))
    elif not _flag(fields.get("enableSched")):
        found.append(("warning", "fields", "Tiene cron pero no está programada (enableSched = 1)."))
    if not str(fields.get("dispatch.earliest_time") or "").strip():
        missing.append("ventana de tiempo (dispatch.earliest_time)")
    counttype = str(fields.get("counttype") or "").strip().lower()
    condition = str(fields.get("alert_condition") or "").strip()
    if not counttype and not condition:
        missing.append("condición de disparo (counttype con relation y quantity, o alert_condition)")
    elif counttype == "custom" and not condition:
        missing.append("condición personalizada (alert_condition)")
    elif counttype and counttype not in ("always", "custom") and not (fields.get("relation") and fields.get("quantity")):
        missing.append("umbral de disparo (relation y quantity)")
    if not str(fields.get("alert.severity") or "").strip():
        missing.append("severidad (alert.severity)")
    actions = alert_actions(fields)
    if not actions:
        missing.append("acciones (actions)")
    if missing:
        found.append(("error", "fields", f"Faltan campos obligatorios: {', '.join(missing)}."))
    if is_test_alert(name):
        if not _flag(fields.get("disabled")):
            found.append(("warning", "test", "La versión TEST viene habilitada: se ejecutará en cuanto se instale la app."))
        if actions and not _flag(fields.get("alert.suppress")):
            found.append(("warning", "test", "La versión TEST no tiene supresión (alert.suppress): puede disparar muchas veces."))
    return found


# --- Registros de macros y lookups del paquete (para resolver dependencias) --------------------------

def package_macros(package, app):
    """Macros del paquete en el formato de core.dependencies (app de la app, compartidas en ella)."""
    return [{"name": name, "app": app, "owner": "nobody", "sharing": "app", "definition": f.get("definition") or "",
             "args": f.get("args") or "", "iseval": _flag(f.get("iseval")), "disabled": _flag(f.get("disabled"))}
            for name, f in package.stanzas("macros").items() if name != "default"]


def package_lookups(package, app):
    """Definiciones de lookup (transforms.conf) y archivos de lookups/ del paquete."""
    records = []
    for name, f in package.stanzas("transforms").items():
        if name != "default" and any(f.get(k) for k in LOOKUP_KEYS):
            records.append({"kind": "definition", "name": name, "type": "file" if f.get("filename") else "other",
                            "filename": f.get("filename") or "", "app": app, "owner": "nobody", "sharing": "app",
                            "disabled": _flag(f.get("disabled"))})
    for path in package.files:
        if path.startswith("lookups/") and path.count("/") == 1:
            records.append({"kind": "file", "name": path[len("lookups/"):], "type": "csv", "app": app, "owner": "nobody",
                            "sharing": "app", "disabled": False})
    return records


def package_searches(package):
    """{nombre: campos efectivos} de las saved searches del paquete (default + local y su [default])."""
    stanzas = package.stanzas("savedsearches")
    return {name: effective_stanza(stanzas, name) for name in stanzas if name != "default"}


def search_rows(package):
    """Saved searches del paquete para elegir cuáles ejecutar: nombre, si es alerta o TEST, ventana, cron y estado."""
    return [{"name": name, "alert": is_alert(name, f), "test": is_test_alert(name),
             "earliest": f.get("dispatch.earliest_time") or "", "latest": f.get("dispatch.latest_time") or "",
             "cron": f.get("cron_schedule") or "", "disabled": _flag(f.get("disabled"))}
            for name, f in package_searches(package).items()]


def macros_by_stanza(records):
    return {m["name"]: m for m in records}


def dependency_problems(searches, context, macros, lookups):
    """
    Macros y lookups con problemas que usan las búsquedas, agrupados: {(tipo, nombre, estado): {item, searches}}, para
    avisar una vez por macro o lookup y no una vez por cada búsqueda que la usa.
    """
    found = {}
    for name, fields in searches.items():
        deps = resolve_dependencies(fields.get("search") or "", context, macros, lookups)
        for item in deps["macros"] + deps["lookups"]:
            if item["status"] in PROBLEMS:
                key = ("macro" if "stanza" in item else "lookup", item.get("stanza") or item["name"], item["status"])
                found.setdefault(key, {"item": item, "searches": []})["searches"].append(name)
    return found


def _used_by(names):
    shown = ", ".join(f"«{n}»" for n in names[:3])
    return f"la usa{'n' if len(names) > 1 else ''} {len(names)} búsqueda{'s' if len(names) > 1 else ''}: {shown}{'…' if len(names) > 3 else ''}"


# --- 1. Validación del paquete -------------------------------------------------------------------

def _app_issues(package):
    issues = []
    info = package.info()
    app_conf = package.stanzas("app")
    if not info["launcher_version"]:
        issues.append(_issue("error", "app", "Falta la versión en app.conf ([launcher] version).", file="default/app.conf"))
    elif not VERSION.match(info["launcher_version"]):
        issues.append(_issue("warning", "app", f"La versión «{info['launcher_version']}» no tiene el formato X.Y.Z.", file="default/app.conf"))
    if info["id_version"] and info["launcher_version"] and info["id_version"] != info["launcher_version"]:
        issues.append(_issue("error", "app", f"Las versiones de [id] ({info['id_version']}) y [launcher] ({info['launcher_version']}) "
                             "no coinciden.", file="default/app.conf"))
    declared = (app_conf.get("package") or {}).get("id") or (app_conf.get("id") or {}).get("name")
    if package.root and declared and declared != package.root:
        issues.append(_issue("error", "app", f"La carpeta del paquete «{package.root}» no coincide con el id de la app «{declared}».",
                             file="default/app.conf"))
    if not info["label"]:
        issues.append(_issue("warning", "app", "Falta el nombre visible de la app ([ui] label).", file="default/app.conf"))
    for note in package.meta.get("notes") or getattr(package, "notes", []):
        issues.append(_issue("warning", "app", note))
    if package.has_local():
        files = sorted(p for p in package.files if p.startswith("local/") or p == "metadata/local.meta")
        issues.append(_issue("warning", "app", f"El paquete incluye local/ ({', '.join(files[:5])}{'…' if len(files) > 5 else ''}): "
                             "AppInspect lo rechaza en Splunk Cloud. Pase esos cambios a default/.", file="local/"))
    return issues


def _csv_issues(path, text):
    issues = []
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        return [_issue("warning", "lookups", "El CSV está vacío.", file=path)]
    except csv.Error as e:
        return [_issue("error", "lookups", f"El CSV no se puede leer: {e}.", file=path)]
    if any(not h.strip() for h in header):
        issues.append(_issue("error", "lookups", "Hay columnas sin nombre en la cabecera.", file=path, line=1))
    repeated = sorted({h for h in header if header.count(h) > 1})
    if repeated:
        issues.append(_issue("error", "lookups", f"Columnas repetidas en la cabecera: {', '.join(repeated)}.", file=path, line=1))
    uneven = []
    try:
        for row in reader:
            if row and len(row) != len(header):
                uneven.append(reader.line_num)
    except csv.Error as e:
        issues.append(_issue("error", "lookups", f"El CSV no se puede leer entero: {e}.", file=path, line=reader.line_num))
    if uneven:
        lines = ", ".join(str(n) for n in uneven[:5])
        rows = f"{len(uneven)} filas" if len(uneven) > 1 else "1 fila"
        issues.append(_issue("warning", "lookups", f"{rows} con un nº de columnas distinto del de la cabecera ({len(header)}): "
                             f"línea{'s' if len(uneven) > 1 else ''} {lines}{'…' if len(uneven) > 5 else ''}.", file=path, line=uneven[0]))
    return issues


def validate_package(package):
    """Problemas de un paquete sin consultar ningún Splunk (lista de issues)."""
    issues = _app_issues(package)
    for path in sorted(package.files):
        if not is_text_path(path):
            continue
        text, encoding = package.text(path)
        issues += [{**i, "source": "paquete"} for i in encoding]
        conf = None
        if path in ("metadata/default.meta", "metadata/local.meta"):
            conf = package.conf(path[len("metadata/"):-len(".meta")], None)
        elif re.fullmatch(r"(default|local)/[^/]+\.conf", path):
            layer, name = path[:-len(".conf")].split("/")
            conf = package.conf(layer, name)
        if conf:
            issues += [{**i, "source": "paquete"} for i in conf.issues]
        elif path.startswith("lookups/") and path.lower().endswith(".csv"):
            issues += _csv_issues(path, text)
        elif re.match(r"^(default|local)/data/ui/(views|nav)/.+\.xml$", path):
            try:
                ElementTree.fromstring(text)
            except ElementTree.ParseError as e:
                line = e.position[0] if getattr(e, "position", None) else None
                issues.append(_issue("error", "views", f"XML mal formado: {e}.", file=path, line=line))

    app = package.info()["id"]
    searches = package_searches(package)
    for name, fields in searches.items():
        file = "local/savedsearches.conf" if name in package.stanzas("savedsearches", "local") else "default/savedsearches.conf"
        alert = is_alert(name, fields)
        if not str(fields.get("search") or "").strip():
            issues.append(_issue("error", "fields", "No tiene búsqueda (search).", name, file))
        for problem in name_problems(name):
            issues.append(_issue("error" if alert else "warning", "naming", problem, name, file))
        if alert:
            for level, category, message in alert_problems(name, fields):
                issues.append(_issue(level, category, message, name, file))
    found = dependency_problems(searches, {"app": app, "owner": "nobody"}, package_macros(package, app), package_lookups(package, app))
    for (kind, name, status), problem in sorted(found.items()):
        item, used = problem["item"], _used_by(problem["searches"])
        label = f"La macro {name}" if kind == "macro" else f"El lookup {name}"
        if status in ("missing", "not_visible"):
            hint = f" (sí está con otro nº de argumentos: {', '.join(item['other_arities'])})" if item.get("other_arities") else ""
            issues.append(_issue("warning", "dependencies", f"{label} no está en el paquete{hint}; {used}. Valide contra un cliente "
                                 "para saber si existe allí.", name))
        elif status == "disabled":
            issues.append(_issue("error", "dependencies", f"{label} viene deshabilitad{'a' if kind == 'macro' else 'o'} en el paquete; {used}.", name))
        else:
            issues.append(_issue("warning", "dependencies", f"{label} apunta a {item.get('filename')}, que no está en lookups/ del "
                                 f"paquete; {used}.", name))
    for name, fields in package.stanzas("transforms").items():
        filename = fields.get("filename")
        if name != "default" and filename and f"lookups/{filename}" not in package.files:
            issues.append(_issue("warning", "lookups", f"La definición de lookup apunta a {filename}, que no está en lookups/ del paquete.",
                                 name, "default/transforms.conf"))
    return issues


def summary(package):
    """Resumen que se guarda al subir un paquete: nº de errores, avisos e info de la validación del paquete."""
    return counts(validate_package(package))


# --- SPL: normalizar, comprobar con el parser y ejecutar ---------------------------------------------

def search_query(spl):
    """SPL tal como lo necesita la API de jobs: con «search» delante si no empieza por un comando con «|»."""
    query = str(spl or "").strip()
    if query and not query.startswith("|") and not query.lower().startswith("search "):
        query = f"search {query}"
    return query


def window(fields, mode, fixed):
    """(earliest, latest) de la ejecución: la ventana de la alerta (en histórico si es en tiempo real) o el rango fijo."""
    if mode == "fixed":
        return fixed.get("earliest") or DEFAULT_EARLIEST, fixed.get("latest") or "now"
    earliest = str(fields.get("dispatch.earliest_time") or "").strip() or DEFAULT_EARLIEST
    latest = str(fields.get("dispatch.latest_time") or "").strip() or "now"
    if earliest.startswith("rt"):
        earliest = earliest[2:] or DEFAULT_EARLIEST
    if latest.startswith("rt"):
        latest = latest[2:] or "now"
    return earliest, latest


def _splunk_message(error):
    body = getattr(error, "body", b"") or b""
    try:
        data = json.loads(body.decode("utf-8") if isinstance(body, bytes) else body)
        texts = [m.get("text", "") for m in data.get("messages", []) if m.get("text")]
        if texts:
            return " ".join(texts)
    except (ValueError, AttributeError):
        pass
    return str(error)


def parse_spl(service, query, app):
    """{ok, commands, message}: el SPL pasado por el parser de Splunk (parse_only: no ejecuta ni las subbúsquedas)."""
    if len(query) > MAX_PARSER_URL:
        return {"ok": None, "commands": [], "message": "SPL demasiado largo para comprobarlo con el parser."}
    try:
        response = service.get("search/parser", owner="nobody", app=app, q=query, output_mode="json",
                               parse_only="true", enable_lookups="false")
        data = json.loads(response.body.read().decode("utf-8") or "{}")
    except HTTPError as e:
        return {"ok": False, "commands": [], "message": _splunk_message(e)}
    found = [c.get("command") for c in data.get("commands", []) if isinstance(c, dict) and c.get("command")]
    return {"ok": True, "commands": found, "message": ""}


def _int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def run_search(service, spl, earliest, latest, app, timeout):
    """
    Ejecuta un SPL como búsqueda ad hoc (nunca una saved search, así que no hay acciones) y devuelve {status, message,
    result_count, event_count, scan_count, duration, messages, query}. status: ok, warning, error, blocked, timeout o
    cancelled. Antes se comprueba que no escribe ni envía datos (en el texto y en los comandos que da el parser) y el job
    se borra al terminar.
    """
    query = search_query(spl)
    result = {"query": query, "earliest": earliest, "latest": latest, "result_count": None, "event_count": None,
              "scan_count": None, "duration": None, "messages": []}
    if not query:
        return {**result, "status": "error", "message": "No tiene búsqueda."}
    blocked = blocked_commands(commands(query))
    if blocked:
        return {**result, "status": "blocked", "message": f"No se ejecuta: usa {', '.join(blocked)}, que escribe o envía datos."}
    parsed = parse_spl(service, query, app)
    if parsed["ok"] is False:
        return {**result, "status": "error", "message": f"El parser de Splunk lo rechaza: {parsed['message']}"}
    blocked = blocked_commands(parsed["commands"])
    if blocked:
        return {**result, "status": "blocked",
                "message": f"No se ejecuta: con las macros del cliente usa {', '.join(blocked)}, que escribe o envía datos."}

    job = service.jobs.create(query, exec_mode="normal", earliest_time=earliest, latest_time=latest, timeout=JOB_TTL,
                              app=app, owner="nobody")
    started = time.time()
    try:
        while True:
            time.sleep(POLL_SECONDS)
            job.refresh()
            content = job.content
            if _flag(content.get("isDone")):
                break
            if cancel_requested():
                return {**result, "status": "cancelled", "message": "Cancelada.", "duration": time.time() - started}
            if time.time() - started > timeout:
                return {**result, "status": "timeout", "message": f"Superó el tiempo máximo ({timeout} s): se canceló.",
                        "duration": time.time() - started}
        messages = _job_messages(content.get("messages"))
        result.update(result_count=_int(content.get("resultCount")), event_count=_int(content.get("eventCount")),
                      scan_count=_int(content.get("scanCount")), duration=float(content.get("runDuration") or 0),
                      messages=messages)
        if _flag(content.get("isFailed")):
            errors = [m for m in messages if m.lower().startswith(("error", "fatal"))]
            return {**result, "status": "error", "message": " ".join(errors or messages) or "La búsqueda falló."}
        warnings = [m for m in messages if m.lower().startswith(("warn", "error"))]
        return {**result, "status": "warning" if warnings else "ok", "message": " ".join(warnings)}
    finally:
        try:
            job.cancel()   # borra el job y sus resultados
        except Exception:
            pass


# --- 2. Validación contra un cliente (tarea) ------------------------------------------------------------

def _client_context(service, app):
    installed = installed_app(service, app)
    return installed, app if installed else "search"


def build_check(package_id, client):
    """(título, función) de la tarea que valida un paquete contra un cliente y guarda el resultado en checks/."""
    package = app_packages.load_package(package_id)
    info = package.info()
    app = info["id"]

    def run():
        created = time.time()
        print(f"▶ Validando {info['label'] or app} {info['version']} contra {client}")
        service = services.get_service(client)
        installed, namespace = _client_context(service, app)
        issues = []
        if installed:
            print(f"  · Versión instalada en {client}: {installed['version'] or '(sin versión)'}")
            if installed["version"] and not is_newer(info["version"], installed["version"]):
                issues.append(_issue("warning", "app", f"La versión del paquete ({info['version']}) no es mayor que la instalada "
                                     f"en {client} ({installed['version']}).", source=client))
        else:
            print(f"  · La app no está instalada en {client}: se usa el contexto de la app search.")
        # Las macros y lookups que el cliente tiene en esta app se sustituirán por los del paquete al desplegarlo
        client_macros = [m for m in macro_records(service) if m["app"] != app]
        client_lookups = [lk for lk in lookup_records(service) if lk["app"] != app]
        searches = package_searches(package)
        found = dependency_problems(searches, {"app": app, "owner": "nobody"}, package_macros(package, app) + client_macros,
                                    package_lookups(package, app) + client_lookups)
        for (kind, name, status), problem in sorted(found.items()):
            item, used = problem["item"], _used_by(problem["searches"])
            label = f"La macro {name}" if kind == "macro" else f"El lookup {name}"
            if status == "missing":
                hint = f" (sí está con otro nº de argumentos: {', '.join(item['other_arities'])})" if item.get("other_arities") else ""
                message = f"{label} no existe ni en el paquete ni en {client}{hint}"
            elif status == "not_visible":
                where = ", ".join(sorted({w["app"] for w in item.get("not_visible_in", [])}))
                message = f"{label} solo está en {where} y no se ve desde {app}"
            elif status == "disabled":
                message = f"{label} está deshabilitad{'a' if kind == 'macro' else 'o'}"
            else:
                message = f"{label} apunta a {item.get('filename')}, que no existe o no se ve"
            issues.append(_issue("error", "dependencies", f"{message}; {used}.", name, source=client))
            print(f"  ❌ {message}.")
        pkg_macros = macros_by_stanza(package_macros(package, app))
        checked = []
        for position, (name, fields) in enumerate(searches.items(), 1):
            if cancel_requested():
                print("⚠️ Cancelada: no se revisan las búsquedas que faltan.")
                break
            set_progress(position - 1, len(searches), name)
            query, _ = expand_macros(fields.get("search") or "", pkg_macros)
            parsed = parse_spl(service, search_query(query), namespace)
            if parsed["ok"] is False:
                issues.append(_issue("error", "spl", f"El parser de Splunk rechaza el SPL: {parsed['message']}", name, source=client))
            elif parsed["ok"] is None:
                issues.append(_issue("info", "spl", parsed["message"], name, source=client))
            blocked = blocked_commands(commands(query) + parsed["commands"])
            if blocked:
                issues.append(_issue("info", "spl", f"Usa {', '.join(blocked)}: al validar no se ejecutará.", name, source=client))
            checked.append({"name": name, "parse": parsed["ok"], "message": parsed["message"], "commands": parsed["commands"]})
            print(f"  {'✅' if parsed['ok'] is not False else '❌'} {name}")
        set_progress(len(checked), len(searches))
        result = {"client": client, "created": created, "finished": time.time(), "installed": installed,
                  "status": "cancelled" if cancel_requested() else "done", "issues": issues, "searches": checked,
                  "counts": counts(issues)}
        result_id = app_packages.save_result(package_id, "checks", result)
        job = current_job()
        if job:
            job.meta["result"] = {"package": package_id, "kind": "checks", "id": result_id}
        c = result["counts"]
        print(f"\nResumen: {c['error']} errores, {c['warning']} avisos en {client} ({len(checked)} búsquedas revisadas).")

    return f"Validación de {app} {info['version']} en {client}", run


def is_newer(version, than):
    """True si la versión 'version' es mayor que 'than' (1.10.0 > 1.9.2; si no se pueden comparar, True)."""
    def key(text):
        return [(0, int(p), "") if p.isdigit() else (1, 0, p) for p in re.split(r"[.-]", str(text or "").strip()) if p]
    try:
        return key(version) > key(than)
    except TypeError:
        return True


# --- 3. Ejecución de búsquedas (tarea) --------------------------------------------------------------------

def run_options(options):
    """Opciones de ejecución validadas: modo de ventana, rango fijo, ejecutar la anterior, macros y tiempo máximo."""
    options = options or {}
    mode = "fixed" if options.get("range") == "fixed" else "window"
    fixed = {"earliest": str(options.get("earliest") or DEFAULT_EARLIEST).strip(), "latest": str(options.get("latest") or "now").strip()}
    for value in fixed.values():
        if not re.fullmatch(r"[\w@+\-.:/ ]{1,40}", value):
            raise ValueError(f"Rango de tiempo no válido: «{value}».")
    try:
        timeout = int(options.get("timeout") or DEFAULT_TIMEOUT)
    except (TypeError, ValueError):
        raise ValueError("Tiempo máximo no válido.")
    if not 10 <= timeout <= MAX_TIMEOUT:
        raise ValueError(f"El tiempo máximo por búsqueda debe estar entre 10 y {MAX_TIMEOUT} segundos.")
    return {"range": mode, "fixed": fixed, "previous": bool(options.get("previous", True)),
            "expand": bool(options.get("expand", True)), "timeout": timeout}


def build_run(package_id, client, names, options, base=None):
    """
    (título, función) de la tarea que ejecuta en 'client' el SPL de las saved searches 'names' del paquete y, con
    previous, también su versión anterior: la de otro paquete subido (base={"package": id}) o la que tiene el cliente.
    """
    package = app_packages.load_package(package_id)
    info = package.info()
    app = info["id"]
    searches = package_searches(package)
    names = [n for n in dict.fromkeys(names or []) if n in searches]
    if not names:
        raise ValueError("Elija al menos una búsqueda del paquete.")
    opts = run_options(options)
    base_package = None
    if opts["previous"] and base and base.get("package"):
        base_package = app_packages.load_package(base["package"])

    def run():
        created = time.time()
        service = services.get_service(client)
        installed, namespace = _client_context(service, app)
        print(f"▶ Ejecutando {len(names)} búsquedas de {app} {info['version']} en {client} (contexto: app {namespace})")
        print("  Solo búsquedas ad hoc: nunca se lanza la saved search, así que no se disparan sus acciones.")
        pkg_macros = macros_by_stanza(package_macros(package, app)) if opts["expand"] else {}
        previous = {}
        if opts["previous"]:
            if base_package:
                old_macros = macros_by_stanza(package_macros(base_package, app)) if opts["expand"] else {}
                for name, fields in package_searches(base_package).items():
                    previous[name] = (expand_macros(fields.get("search") or "", old_macros)[0], fields, f"versión {base_package.info()['version']}")
            elif installed:
                for entry in _client_searches(service, app):
                    previous[entry["name"]] = (entry["search"], entry, f"{client} (instalada {installed['version']})")
        results = []
        for position, name in enumerate(names, 1):
            if cancel_requested():
                print("⚠️ Cancelada: no se ejecutan las búsquedas que faltan.")
                break
            set_progress(position - 1, len(names), name)
            fields = searches[name]
            earliest, latest = window(fields, opts["range"], opts["fixed"])
            spl, expanded = expand_macros(fields.get("search") or "", pkg_macros)
            print(f"\n[{position}/{len(names)}] {name}  ({earliest} → {latest})")
            now = run_search(service, spl, earliest, latest, namespace, opts["timeout"])
            now["expanded_macros"] = expanded
            item = {"name": name, **now, "previous": None}
            print(f"  {_line(now)}")
            if name in previous and now["status"] not in ("blocked", "cancelled") and not cancel_requested():
                old_spl, old_fields, label = previous[name]
                old_earliest, old_latest = window(old_fields, opts["range"], opts["fixed"])
                before = run_search(service, old_spl, old_earliest, old_latest, namespace, opts["timeout"])
                item["previous"] = {**before, "label": label}
                print(f"  Anterior ({label}): {_line(before)}")
            results.append(item)
        set_progress(len(results), len(names))
        status_counts = {}
        for r in results:
            status_counts[r["status"]] = status_counts.get(r["status"], 0) + 1
        result = {"client": client, "created": created, "finished": time.time(), "installed": installed,
                  "status": "cancelled" if cancel_requested() else "done", "options": opts, "base": base or {"client": client},
                  "results": results, "counts": status_counts}
        result_id = app_packages.save_result(package_id, "runs", result)
        job = current_job()
        if job:
            job.meta["result"] = {"package": package_id, "kind": "runs", "id": result_id}
        print(f"\nResumen: {', '.join(f'{v} {k}' for k, v in status_counts.items()) or 'nada ejecutado'}.")

    what = f"«{names[0]}»" if len(names) == 1 else f"{len(names)} búsquedas"
    return f"Ejecución de {what} de {app} {info['version']} en {client}", run


def _client_searches(service, app):
    """Saved searches que tiene el cliente en la app (nombre, SPL y ventana), para ejecutar la versión instalada."""
    data = _read_json(service, "saved/searches", count=0, owner="nobody", app=app,
                      f=["search", "dispatch.earliest_time", "dispatch.latest_time"])
    return [{"name": e.get("name"), **(e.get("content") or {})} for e in data.get("entry", [])
            if (e.get("acl") or {}).get("app") == app]


def _line(result):
    icons = {"ok": "✅", "warning": "⚠️", "error": "❌", "blocked": "⛔", "timeout": "⏱️", "cancelled": "⏹"}
    parts = [icons.get(result["status"], "·")]
    if result["result_count"] is not None:
        parts.append(f"{result['result_count']} resultados en {result['duration']:.1f} s")
    if result.get("message"):
        parts.append(result["message"] if result["status"] != "error" else f"Error: {result['message']}")
    return " ".join(parts)
