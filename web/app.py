"""Aplicación Flask de Splunk Manager: API JSON + página única (web/templates/index.html)."""
import hmac
import os

from flask import Flask, Response, abort, jsonify, render_template, request, send_file
from splunklib.binding import AuthenticationError, HTTPError
from werkzeug.exceptions import HTTPException

from core import acs, audit
from core.clients import ensure_config_exists, migrate_config
from core.conventions import SIRT_APP, SIRT_APP_LABEL
from core.paths import backup_dir
from options.alerts.backup_alerts import TECNOLOGIAS
from web import (
    access, addons, alert_analysis, alert_editor, app_diff, app_packages, app_report, app_validation, compare, dependencies,
    explorer, hec, lookups, macro_editor, multi_edit, operations, platform, reports, services,
)
from web.health import monitor as health_monitor
from web.jobs import install_output_router, jobs
from web.operations import BACKUP_FOLDERS, OperationError
from web.services import ClientError

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
CSRF_HEADER = "X-Splunk-Manager"

RESOURCES = {
    "saved-searches": explorer.list_saved_searches,
    "macros": explorer.list_macros,
    "lookups": lookups.list_lookups,
    "users": explorer.list_users,
    "roles": explorer.list_roles,
    "dashboards": explorer.list_dashboards,
    "indexes": explorer.list_indexes,
    "apps": explorer.list_apps,
    "fired-alerts": explorer.list_fired_alerts,
}


def create_app(password=None, restrict_host=True):
    """
    password: si se indica, se exige autenticación HTTP Basic (cualquier usuario + esa contraseña).
    restrict_host: solo acepta peticiones dirigidas a localhost (protege frente a DNS rebinding).
    """
    app = Flask(__name__)
    app.json.sort_keys = False
    app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024   # paquetes de app (como mucho 100 MB) y backups JSON
    install_output_router()
    ensure_config_exists()
    try:
        for message in migrate_config():  # Cifra las credenciales que sigan en claro en config.py
            print(message)
    except Exception as e:
        print(f"❌ No se pudieron cifrar las credenciales de config.py: {e}")
    backup_dir()

    @app.before_request
    def security_checks():
        if restrict_host:
            if request.host.startswith("["):
                host = request.host.split("]")[0] + "]"   # IPv6: [::1]:puerto
            else:
                host = request.host.rsplit(":", 1)[0]
            if host not in LOOPBACK_HOSTS:
                abort(403)
        if password:
            auth = request.authorization
            if not auth or not hmac.compare_digest(auth.password or "", password):
                return ("Autenticación requerida", 401, {"WWW-Authenticate": 'Basic realm="Splunk Manager"'})
        # Las peticiones que modifican algo deben venir de la propia página (evita CSRF desde otras webs)
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get(CSRF_HEADER) != "1":
            abort(403)

    @app.errorhandler(ClientError)
    @app.errorhandler(OperationError)
    @app.errorhandler(ValueError)
    def bad_request(e):
        return jsonify(error=str(e)), 400

    @app.errorhandler(AuthenticationError)
    def splunk_auth_error(e):
        return jsonify(error=f"Splunk rechazó las credenciales: {e}"), 502

    @app.errorhandler(HTTPError)
    def splunk_http_error(e):
        return jsonify(error=f"Splunk respondió con un error: {e}"), 502

    @app.errorhandler(KeyError)
    def not_found(e):
        return jsonify(error=f"No encontrado: {e}"), 404

    @app.errorhandler(Exception)
    def unexpected(e):
        if isinstance(e, HTTPException):  # 404, 403... generados por Flask
            return jsonify(error=e.description), e.code
        app.logger.exception("Error no controlado")
        return jsonify(error=f"{type(e).__name__}: {e}"), 500

    def body():
        return request.get_json(silent=True) or {}

    def client_param():
        name = request.args.get("client") or body().get("client")
        if not name:
            raise ClientError("No se ha indicado ningún cliente.")
        return name

    # --- Página ---

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/meta")
    def meta():
        return jsonify(
            tecnologias=TECNOLOGIAS,
            folders={k: {"label": v["label"], "prefix": v["prefix"], "itemized": bool(v["key"])}
                     for k, v in BACKUP_FOLDERS.items()},
            sharing=list(operations.SHARING_LEVELS),
            compare_types=compare.types_meta(),
            report_sections=reports.SECTIONS,
            report_formats=reports.formats(),
            analysis_days=list(alert_analysis.ANALYSIS_DAYS),
            sirt_app=SIRT_APP,
            sirt_app_label=SIRT_APP_LABEL,
            allowlist_features=access.features(),
            hec_fields=list(hec.EDITABLE),
            token_warn_days={"warning": platform.TOKEN_WARN_DAYS, "serious": platform.TOKEN_SERIOUS_DAYS},
        )

    # --- Clientes ---

    @app.get("/api/clients")
    def clients_list():
        return jsonify(services.list_clients())

    def simulate():
        """La petición pide solo simular (mostrar lo que se enviaría a ACS sin tocar nada)."""
        return bool(body().get("simulate")) or request.args.get("simulate") == "1"

    @app.post("/api/clients")
    def clients_create():
        data = body()
        result = services.create_client(data)
        audit.record("client.create", f"Alta del cliente {result['name']}", result["name"], {"host": result["host"]})
        return jsonify(result), 201

    @app.get("/api/clients/<path:name>/platform")
    def clients_platform(name):
        """Plataforma del cliente: tipo, ACS, token y (si se pide) estado del stack vía ACS."""
        client = services.find_client(name)
        info = services.platform_of(client, services.server_info(name))
        if request.args.get("stack") == "1" and info["acs"]:
            try:
                services.stack_status(name, force=request.args.get("force") == "1")
                info = services.platform_of(client, services.server_info(name))
            except acs.AcsError as e:
                info["stack_error"] = str(e)
        return jsonify(info)

    @app.post("/api/clients/test")
    def clients_test():
        return jsonify(services.test_credentials(body()))

    @app.put("/api/clients/<path:name>")
    def clients_update(name):
        data = body()
        result = services.update_client(name, data)
        audit.record("client.update", f"Modificar el cliente {name}", result["name"],
                     {"host": result["host"], "renamed": result["name"] if result["name"] != name else None,
                      "password": "cambiada" if data.get("password") else ("quitada" if data.get("clear_password") else None),
                      "token": "cambiado" if data.get("splunkToken") else ("quitado" if data.get("clear_token") else None)})
        return jsonify(result)

    @app.delete("/api/clients/<path:name>")
    def clients_delete(name):
        services.delete_client(name)
        audit.record("client.delete", f"Baja del cliente {name}", name)
        return jsonify(ok=True)

    @app.post("/api/clients/<path:name>/connect")
    def clients_connect(name):
        services.connect(name, force=True)
        return jsonify(services.public_client(services.find_client(name)))

    @app.post("/api/clients/<path:name>/disconnect")
    def clients_disconnect(name):
        services.disconnect(name)
        return jsonify(ok=True)

    # --- Objetos de Splunk en vivo ---

    @app.get("/api/splunk/<resource>")
    def splunk_list(resource):
        if resource not in RESOURCES:
            abort(404)
        service = services.get_service(client_param())
        return jsonify(RESOURCES[resource](service))

    @app.post("/api/splunk/saved-searches/action")
    def saved_search_action():
        data = body()
        client = client_param()
        service = services.get_service(client)
        result = explorer.saved_search_action(
            service, data.get("name"), data.get("app"), data.get("owner"), data.get("action"), data.get("sharing"))
        audit.record(f"alert.{data.get('action')}", f"{str(data.get('action') or '').capitalize()} «{data.get('name')}»", client,
                     {"app": data.get("app"), "owner": data.get("owner")})
        return jsonify(result)

    @app.get("/api/splunk/saved-searches/detail")
    def saved_search_detail():
        service = services.get_service(client_param())
        return jsonify(alert_editor.saved_search_detail(
            service, request.args.get("name"), request.args.get("app"), request.args.get("owner"),
            request.args.get("sharing")))

    @app.post("/api/splunk/saved-searches/update")
    def saved_search_update():
        data = body()
        client = client_param()
        service = services.get_service(client)
        result = alert_editor.update_saved_search(
            service, data.get("name"), data.get("app"), data.get("owner"), data.get("changes"), data.get("sharing"))
        audit.record("alert.update", f"Editar «{data.get('name')}»", client,
                     {"app": data.get("app"), "fields": sorted((data.get("changes") or {}).keys())})
        return jsonify(result)

    @app.post("/api/splunk/saved-searches/bulk-update")
    def saved_search_bulk_update():
        data = body()
        client = client_param()
        service = services.get_service(client)
        items = data.get("items") or []
        result = alert_editor.bulk_update(service, items, data.get("changes"))
        audit.record("alert.bulk-update", f"Editar en bloque {len(items)} alertas o reportes", client,
                     {"names": [i.get("name") for i in items], "fields": sorted((data.get("changes") or {}).keys())})
        return jsonify(result)

    @app.get("/api/splunk/saved-searches/dependencies")
    def saved_search_dependencies():
        service = services.get_service(client_param())
        return jsonify(dependencies.saved_search_dependencies(
            service, request.args.get("name"), request.args.get("app"), request.args.get("owner"),
            request.args.get("sharing")))

    @app.get("/api/splunk/lookups/usage")
    def lookup_usage():
        service = services.get_service(client_param())
        return jsonify(lookups.lookup_usage(service, request.args.get("name"), request.args.get("kind")))

    @app.post("/api/splunk/lookups/preview")
    def lookup_preview():
        data = body()
        service = services.get_service(client_param())
        return jsonify(lookups.lookup_preview(service, data.get("name"), data.get("app"), data.get("owner"),
                                              data.get("sharing")))

    @app.get("/api/splunk/macros/detail")
    def macro_detail():
        service = services.get_service(client_param())
        return jsonify(macro_editor.macro_detail(
            service, request.args.get("name"), request.args.get("app"), request.args.get("owner"),
            request.args.get("sharing")))

    @app.post("/api/splunk/macros/update")
    def macro_update():
        data = body()
        client = client_param()
        service = services.get_service(client)
        result = macro_editor.update_macro(
            service, data.get("name"), data.get("app"), data.get("owner"), data.get("changes"), data.get("sharing"))
        audit.record("macro.update", f"Editar la macro «{data.get('name')}»", client,
                     {"app": data.get("app"), "fields": sorted((data.get("changes") or {}).keys())})
        return jsonify(result)

    @app.post("/api/splunk/macros/bulk-update")
    def macro_bulk_update():
        data = body()
        client = client_param()
        service = services.get_service(client)
        items = data.get("items") or []
        result = macro_editor.bulk_update(service, items, data.get("changes"))
        audit.record("macro.bulk-update", f"Editar en bloque {len(items)} macros", client,
                     {"names": [i.get("name") for i in items], "fields": sorted((data.get("changes") or {}).keys())})
        return jsonify(result)

    @app.get("/api/ingest")
    def ingest():
        service = services.get_service(client_param())
        return jsonify(explorer.ingest_usage(service, int(request.args.get("days", 30))))

    @app.get("/api/alert-analysis")
    def alert_analysis_get():
        name = client_param()
        service = services.get_service(name)
        return jsonify(alert_analysis.analyze_alerts(service, int(request.args.get("days", 7)), client=services.find_client(name)))

    # --- Salud de todos los clientes ---

    @app.get("/api/health")
    def health_get():
        return jsonify(health_monitor.snapshot())

    @app.post("/api/health/run")
    def health_run():
        known = [c["name"] for c in services.list_clients()]
        requested = body().get("clients") or known
        health_monitor.run([c for c in requested if c in known])
        return jsonify(health_monitor.snapshot()), 202

    # --- Búsqueda SPL ---

    @app.post("/api/search")
    def search_start():
        data = body()
        service = services.get_service(client_param())
        sid = explorer.start_search(service, data.get("query"), data.get("earliest"), data.get("latest"))
        return jsonify(sid=sid)

    @app.get("/api/search/<sid>")
    def search_get(sid):
        service = services.get_service(client_param())
        max_results = min(int(request.args.get("max", explorer.DEFAULT_MAX_RESULTS)), 50000)
        return jsonify(explorer.search_status(service, sid, max_results))

    @app.post("/api/search/<sid>/control")
    def search_control(sid):
        service = services.get_service(client_param())
        explorer.search_control(service, sid, body().get("action"))
        return jsonify(ok=True)

    # --- Operaciones (tareas en segundo plano) ---

    def target_clients(data):
        clients = [c for c in data.get("clients") or [] if c]
        if not clients:
            raise OperationError("Seleccione al menos un cliente.")
        known = {c["name"] for c in services.list_clients()}
        unknown = [c for c in clients if c not in known]
        if unknown:
            raise OperationError(f"Clientes desconocidos: {', '.join(unknown)}")
        return clients

    def clients_suffix(clients):
        return clients[0] if len(clients) == 1 else f"{len(clients)} clientes"

    @app.post("/api/ops/backup")
    def ops_backup():
        data = body()
        clients = target_clients(data)
        title, action = operations.build_backup(data.get("kind"), data.get("options") or {})
        job = jobs.start(f"{title} · {clients_suffix(clients)}", "backup", clients,
                         operations.run_for_clients(clients, action))
        return jsonify(job.summary()), 202

    @app.post("/api/ops/restore")
    def ops_restore():
        data = body()
        clients = target_clients(data)
        title, action = operations.build_restore(data.get("folder"), data.get("files") or [], data.get("options") or {})
        job = jobs.start(f"{title} · {clients_suffix(clients)}", "restore", clients,
                         operations.run_for_clients(clients, action))
        return jsonify(job.summary()), 202

    # --- Edición de alertas en varios clientes ---

    @app.post("/api/multi-edit/lookup")
    def multi_edit_lookup():
        data = body()
        return jsonify(multi_edit.lookup(data.get("names"), target_clients(data)))

    @app.post("/api/multi-edit/preview")
    def multi_edit_preview():
        data = body()
        return jsonify(multi_edit.preview(data.get("names"), target_clients(data), data.get("ops")))

    @app.post("/api/multi-edit/apply")
    def multi_edit_apply():
        data = body()
        title, clients, action = multi_edit.build_apply(data.get("names"), data.get("ops"), data.get("targets"))
        target_clients({"clients": clients})
        job = jobs.start(title, "edit", clients, action)
        return jsonify(job.summary()), 202

    @app.post("/api/compare")
    def compare_run():
        data = body()
        return jsonify(compare.compare_clients(data.get("type"), data.get("reference"), data.get("targets") or [],
                                               data.get("fields") or "main", bool(data.get("pair_test"))))

    @app.post("/api/compare/copy")
    def compare_copy():
        data = body()
        target_clients({"clients": [data.get("source")] + list(data.get("targets") or [])})
        title, action = compare.build_copy(data.get("type"), data.get("source"), data.get("targets"),
                                           data.get("names"), data.get("options") or {})
        job = jobs.start(title, "copy", list(data.get("targets") or []), action)
        return jsonify(job.summary()), 202

    # --- Informes ---

    @app.get("/api/reports")
    def reports_list():
        return jsonify(reports.list_reports())

    @app.post("/api/reports")
    def reports_create():
        data = body()
        clients = target_clients(data)
        title, action = reports.build_report(clients, data.get("sections"), data.get("formats"), data.get("options") or {})
        job = jobs.start(title, "report", clients, action)
        return jsonify(job.summary()), 202

    @app.get("/api/reports/<name>")
    def reports_download(name):
        path = reports.report_path(name)
        inline = request.args.get("inline") == "1" and not name.endswith(".xlsx")
        return send_file(path, as_attachment=not inline, download_name=name)

    @app.delete("/api/reports/<name>")
    def reports_delete(name):
        os.remove(reports.report_path(name))
        return jsonify(ok=True)

    @app.get("/api/jobs")
    def jobs_list():
        return jsonify(jobs.list())

    @app.get("/api/jobs/<job_id>")
    def jobs_get(job_id):
        job = jobs.get(job_id)
        if not job:
            abort(404)
        return jsonify(job.detail(int(request.args.get("since", 0))))

    @app.post("/api/jobs/<job_id>/cancel")
    def jobs_cancel(job_id):
        if not jobs.cancel(job_id):
            raise OperationError("Esta tarea no se puede cancelar (o ya ha terminado).")
        return jsonify(ok=True)

    # --- Validar app: versiones subidas, validación, cambios, ejecución e informe ---

    def package_base(data):
        """Con qué se compara: otra versión subida ({"package": id}) o un cliente ({"client": nombre})."""
        base = data.get("base") or {}
        if base.get("package"):
            return {"package": str(base["package"])}
        if base.get("client"):
            target_clients({"clients": [base["client"]]})
            return {"client": str(base["client"])}
        return None

    @app.get("/api/app-packages")
    def app_packages_list():
        return jsonify(app_packages.list_packages())

    @app.post("/api/app-packages")
    def app_packages_upload():
        if (request.content_length or 0) > app_packages.MAX_PACKAGE_BYTES:
            raise app_packages.PackageError(f"El paquete ocupa más de {app_packages.MAX_PACKAGE_BYTES // (1024 * 1024)} MB.")
        meta = app_packages.save_package(request.args.get("filename"), request.get_data(cache=False),
                                         summarize=app_validation.summary)
        return jsonify(meta), 200 if meta.get("duplicate") else 201

    @app.get("/api/app-packages/<package_id>")
    def app_packages_detail(package_id):
        package = app_packages.load_package(package_id)
        issues = app_validation.validate_package(package)
        meta = app_packages.update_meta(package_id, summary=app_validation.counts(issues))
        return jsonify(meta=meta, info=package.info(), issues=issues, searches=app_validation.search_rows(package),
                       checks=app_packages.list_results(package_id, "checks"), runs=app_packages.list_results(package_id, "runs"))

    @app.delete("/api/app-packages/<package_id>")
    def app_packages_delete(package_id):
        app_packages.delete_package(package_id)
        return jsonify(ok=True)

    @app.post("/api/app-packages/<package_id>/diff")
    def app_packages_diff(package_id):
        base = package_base(body())
        if not base:
            raise OperationError("Elija con qué comparar: otra versión subida o un cliente.")
        package = app_packages.load_package(package_id)
        if "package" in base:
            if base["package"] == package_id:
                raise OperationError("Elija otra versión distinta para comparar.")
            other = app_packages.load_package(base["package"])
            result = app_diff.diff_packages(package, other)
            result["base"] = {"kind": "package", "id": base["package"], "version": other.info()["version"],
                              "filename": app_packages.get_meta(base["package"])["filename"]}
        else:
            service = services.get_service(base["client"])
            snapshot = app_diff.client_snapshot(service, package.info()["id"], package.conf_names())
            result = app_diff.diff_client(package, snapshot)
            result["base"] = {"kind": "client", "client": base["client"], "installed": snapshot["installed"]}
        for kind in result["types"]:
            kind["label"] = app_diff.type_label(kind["type"])
        return jsonify(result)

    @app.post("/api/app-packages/<package_id>/check")
    def app_packages_check(package_id):
        client = target_clients({"clients": [body().get("client")]})[0]
        title, action = app_validation.build_check(package_id, client)
        job = jobs.start(title, "app-check", [client], action, cancellable=True, meta={"package": package_id})
        return jsonify(job.summary()), 202

    @app.post("/api/app-packages/<package_id>/run")
    def app_packages_run(package_id):
        data = body()
        client = target_clients({"clients": [data.get("client")]})[0]
        title, action = app_validation.build_run(package_id, client, data.get("names") or [], data.get("options") or {},
                                                 package_base(data))
        job = jobs.start(title, "app-run", [client], action, cancellable=True, meta={"package": package_id})
        return jsonify(job.summary()), 202

    @app.get("/api/app-packages/<package_id>/results/<kind>/<result_id>")
    def app_packages_result(package_id, kind, result_id):
        return jsonify(app_packages.read_result(package_id, kind, result_id))

    @app.post("/api/app-packages/<package_id>/report")
    def app_packages_report(package_id):
        data = body()
        options = {**(data.get("options") or {}), "base": package_base(data.get("options") or {})}
        title, action = app_report.build_report(package_id, data.get("formats"), options)
        job = jobs.start(title, "report", [], action, meta={"package": package_id})
        return jsonify(job.summary()), 202

    @app.delete("/api/jobs")
    def jobs_clear():
        jobs.clear_finished()
        return jsonify(ok=True)

    # --- Add-ons y apps de Splunkbase (ACS) ---

    @app.get("/api/addons/pending")
    def addons_pending():
        return jsonify(addons.pending(client_param()))

    @app.get("/api/addons/overview")
    def addons_overview():
        names = [n for n in (request.args.get("clients") or "").split(",") if n]
        return jsonify(addons.overview(names or None))

    @app.post("/api/addons/update")
    def addons_update():
        data = body()
        client = target_clients({"clients": [data.get("client")]})[0]
        title, action = addons.build_update(client, data.get("names") or [])
        job = jobs.start(title, "addons", [client], action, cancellable=True)
        return jsonify(job.summary()), 202

    @app.post("/api/addons/update-many")
    def addons_update_many():
        data = body()
        clients = target_clients(data)
        title, action = addons.build_update_many(clients, data.get("names") or [], bool(data.get("exclude_major")))
        job = jobs.start(title, "addons", clients, action, cancellable=True)
        return jsonify(job.summary()), 202

    @app.get("/api/splunkbase-account")
    def splunkbase_account_get():
        return jsonify(addons.account_status())

    @app.put("/api/splunkbase-account")
    def splunkbase_account_save():
        result = addons.store_account(body())
        audit.record("splunkbase.account", f"Guardar la cuenta de Splunkbase {result.get('username')}", None)
        return jsonify(result)

    @app.delete("/api/splunkbase-account")
    def splunkbase_account_delete():
        audit.record("splunkbase.account", "Quitar la cuenta de Splunkbase", None)
        return jsonify(addons.remove_account())

    # --- Tokens HEC (ACS) ---

    @app.get("/api/hec")
    def hec_list():
        return jsonify(hec.list_tokens(client_param()))

    @app.get("/api/hec/overview")
    def hec_overview():
        names = [n for n in (request.args.get("clients") or "").split(",") if n]
        return jsonify(hec.overview(names or None))

    @app.get("/api/hec/value")
    def hec_value():
        name = request.args.get("name")
        audit.record("hec.reveal", f"Ver el valor del token HEC «{name}»", client_param())
        return jsonify(hec.token_value(client_param(), name))

    @app.post("/api/hec")
    def hec_create():
        data = body()
        return jsonify(hec.create(client_param(), data, dry_run=simulate())), 200 if simulate() else 201

    @app.put("/api/hec/<path:name>")
    def hec_update(name):
        data = body()
        return jsonify(hec.update(client_param(), name, data.get("changes") or {}, dry_run=simulate()))

    @app.post("/api/hec/<path:name>/delete")
    def hec_delete(name):
        return jsonify(hec.delete(client_param(), name, dry_run=simulate()))

    @app.post("/api/hec/backup")
    def hec_backup():
        clients = target_clients(body())
        title, action = hec.build_backup(clients)
        job = jobs.start(title, "backup", clients, action, cancellable=True)
        return jsonify(job.summary()), 202

    # --- Listas de IP y puertos de salida (ACS) ---

    @app.get("/api/access")
    def access_inventory():
        return jsonify(access.inventory(client_param()))

    @app.get("/api/access/overview")
    def access_overview():
        names = [n for n in (request.args.get("clients") or "").split(",") if n]
        return jsonify(access.overview(names or None))

    @app.post("/api/access/change")
    def access_change():
        data = body()
        clients = target_clients(data)
        title, action = access.build_change(data.get("kind"), data.get("action"), clients, data.get("data") or {},
                                            dry_run=bool(data.get("simulate")))
        job = jobs.start(title, "access", clients, action, cancellable=True)
        return jsonify(job.summary()), 202

    @app.post("/api/access/backup")
    def access_backup():
        clients = target_clients(body())
        title, action = operations.build_backup("acceso", {})
        job = jobs.start(f"{title} · {clients_suffix(clients)}", "backup", clients, operations.run_for_clients(clients, action))
        return jsonify(job.summary()), 202

    # --- Plataforma: estado, reinicio, mantenimiento, tokens y exportación de apps (ACS) ---

    @app.get("/api/platform/status")
    def platform_status():
        return jsonify(platform.status(client_param(), force=request.args.get("force") == "1"))

    @app.get("/api/platform/overview")
    def platform_overview():
        names = [n for n in (request.args.get("clients") or "").split(",") if n]
        return jsonify(platform.overview(names or None))

    @app.post("/api/platform/restart")
    def platform_restart():
        client = target_clients({"clients": [body().get("client")]})[0]
        title, action = platform.build_restart(client, dry_run=simulate())
        job = jobs.start(title, "platform", [client], action, cancellable=True)
        return jsonify(job.summary()), 202

    @app.get("/api/platform/tokens")
    def platform_tokens():
        return jsonify(platform.tokens(client_param()))

    @app.get("/api/platform/tokens/overview")
    def platform_tokens_overview():
        names = [n for n in (request.args.get("clients") or "").split(",") if n]
        return jsonify(platform.tokens_overview(names or None, with_acs=request.args.get("acs") == "1"))

    @app.post("/api/platform/tokens")
    def platform_token_create():
        return jsonify(platform.create_token(client_param(), body(), dry_run=simulate()))

    @app.post("/api/platform/tokens/delete")
    def platform_token_delete():
        return jsonify(platform.delete_token(client_param(), body().get("id"), dry_run=simulate()))

    @app.get("/api/platform/exports")
    def platform_exports():
        return jsonify(platform.list_exports(client_param()))

    @app.post("/api/platform/exports")
    def platform_export():
        data = body()
        client = target_clients({"clients": [data.get("client")]})[0]
        title, action = platform.build_export(client, data.get("names") or [], data.get("options") or {})
        job = jobs.start(title, "export", [client], action, cancellable=True)
        return jsonify(job.summary()), 202

    @app.get("/api/platform/exports/<name>")
    def platform_export_download(name):
        path = platform.export_path(client_param(), name)
        return send_file(path, as_attachment=True, download_name=name, mimetype="application/gzip")

    @app.delete("/api/platform/exports/<name>")
    def platform_export_delete(name):
        path = platform.export_path(client_param(), name)
        os.remove(path)
        audit.record("app.export.delete", f"Borrar la exportación {name}", client_param())
        return jsonify(ok=True)

    # --- Auditoría ---

    @app.get("/api/audit")
    def audit_list():
        entries = audit.read(limit=min(int(request.args.get("limit", 500)), audit.MAX_READ), client=request.args.get("client") or None,
                             action=request.args.get("action") or None, text=request.args.get("text") or None)
        return jsonify(entries=entries, actions=audit.actions())

    @app.get("/api/audit/export")
    def audit_export():
        entries = audit.read(limit=0, client=request.args.get("client") or None, action=request.args.get("action") or None,
                             text=request.args.get("text") or None)
        csv_text = "\ufeff" + audit.to_csv(entries)
        return Response(csv_text, mimetype="text/csv; charset=utf-8",
                        headers={"Content-Disposition": "attachment; filename=auditoria.csv"})

    # --- Archivos de backup ---

    @app.get("/api/backups")
    def backups_list():
        return jsonify(operations.describe_backups())

    @app.get("/api/backups/<folder>/<filename>")
    def backups_read(folder, filename):
        return jsonify(operations.read_backup(folder, filename))

    @app.get("/api/backups/<folder>/<filename>/versions")
    def backups_versions(folder, filename):
        return jsonify(operations.backup_versions(folder, filename))

    @app.get("/api/backups/<folder>/<filename>/versions/<version>")
    def backups_version_read(folder, filename, version):
        return jsonify(operations.read_backup(folder, filename, version))

    @app.get("/api/backups/<folder>/<filename>/versions/<version>/download")
    def backups_version_download(folder, filename, version):
        path = operations.version_path(folder, filename, version)
        name = f"{filename[:-5]}_{version}"
        return send_file(path, as_attachment=True, download_name=name, mimetype="application/json")

    @app.get("/api/backups/<folder>/<filename>/diff")
    def backups_diff(folder, filename):
        return jsonify(operations.diff_backup(folder, filename, request.args.get("from"), request.args.get("to", "current")))

    @app.get("/api/backups/<folder>/<filename>/download")
    def backups_download(folder, filename):
        path = operations.backup_path(folder, filename)
        if not os.path.isfile(path):
            abort(404)
        return send_file(path, as_attachment=True, download_name=filename, mimetype="application/json")

    @app.delete("/api/backups/<folder>/<filename>")
    def backups_delete(folder, filename):
        path = operations.backup_path(folder, filename)
        if not os.path.isfile(path):
            abort(404)
        os.remove(path)
        audit.record("backup.delete", f"Borrar el backup {folder}/{filename}", None)
        return jsonify(ok=True)

    @app.post("/api/backups/<folder>")
    def backups_upload(folder):
        data = body()
        name = operations.save_uploaded_backup(folder, data.get("filename"), data.get("content") or "")
        audit.record("backup.upload", f"Importar el backup {folder}/{name}", None)
        return jsonify(name=name), 201

    return app
