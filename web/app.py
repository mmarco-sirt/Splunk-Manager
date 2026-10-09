"""Aplicación Flask de Splunk Manager: API JSON + página única (web/templates/index.html)."""
import hmac
import os

from flask import Flask, abort, jsonify, render_template, request, send_file
from splunklib.binding import AuthenticationError, HTTPError
from werkzeug.exceptions import HTTPException

from core.clients import ensure_config_exists, migrate_config
from core.paths import backup_dir
from options.alerts.backup_alerts import TECNOLOGIAS
from web import alert_editor, compare, explorer, operations, reports, services
from web.health import monitor as health_monitor
from web.jobs import install_output_router, jobs
from web.operations import BACKUP_FOLDERS, OperationError
from web.services import ClientError

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
CSRF_HEADER = "X-Splunk-Manager"

RESOURCES = {
    "saved-searches": explorer.list_saved_searches,
    "macros": explorer.list_macros,
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
        )

    # --- Clientes ---

    @app.get("/api/clients")
    def clients_list():
        return jsonify(services.list_clients())

    @app.post("/api/clients")
    def clients_create():
        return jsonify(services.create_client(body())), 201

    @app.post("/api/clients/test")
    def clients_test():
        return jsonify(services.test_credentials(body()))

    @app.put("/api/clients/<path:name>")
    def clients_update(name):
        return jsonify(services.update_client(name, body()))

    @app.delete("/api/clients/<path:name>")
    def clients_delete(name):
        services.delete_client(name)
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
        service = services.get_service(client_param())
        return jsonify(explorer.saved_search_action(
            service, data.get("name"), data.get("app"), data.get("owner"), data.get("action")))

    @app.get("/api/splunk/saved-searches/detail")
    def saved_search_detail():
        service = services.get_service(client_param())
        return jsonify(alert_editor.saved_search_detail(
            service, request.args.get("name"), request.args.get("app"), request.args.get("owner")))

    @app.post("/api/splunk/saved-searches/update")
    def saved_search_update():
        data = body()
        service = services.get_service(client_param())
        return jsonify(alert_editor.update_saved_search(
            service, data.get("name"), data.get("app"), data.get("owner"), data.get("changes")))

    @app.post("/api/splunk/saved-searches/bulk-update")
    def saved_search_bulk_update():
        data = body()
        service = services.get_service(client_param())
        return jsonify(alert_editor.bulk_update(service, data.get("items") or [], data.get("changes")))

    @app.get("/api/ingest")
    def ingest():
        service = services.get_service(client_param())
        return jsonify(explorer.ingest_usage(service, int(request.args.get("days", 30))))

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

    @app.post("/api/compare")
    def compare_run():
        data = body()
        return jsonify(compare.compare_clients(data.get("type"), data.get("reference"), data.get("targets") or [],
                                               data.get("fields") or "main"))

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

    @app.delete("/api/jobs")
    def jobs_clear():
        jobs.clear_finished()
        return jsonify(ok=True)

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
        return jsonify(ok=True)

    @app.post("/api/backups/<folder>")
    def backups_upload(folder):
        data = body()
        name = operations.save_uploaded_backup(folder, data.get("filename"), data.get("content") or "")
        return jsonify(name=name), 201

    return app
