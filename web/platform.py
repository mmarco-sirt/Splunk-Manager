"""
Plataforma de Splunk Cloud vía ACS: estado del stack y reinicio, ventanas de mantenimiento y change freezes,
tokens de autenticación (caducidad, alta, baja y renovación del token del cliente) y exportación de apps como .tgz.
"""
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

from core import acs, audit
from core.paths import backup_dir
from web import app_packages, services
from web.jobs import cancel_requested, capture_output, set_progress
from web.services import ClientError

CLIENT_WORKERS = 6
RESTART_TIMEOUT = 1800
RESTART_POLL = 20
EXPORT_FOLDER = "apps"
MAX_EXPORTS_PER_APP = 10
SAFE = re.compile(r"[^A-Za-z0-9_.-]+")
TOKEN_WARN_DAYS = 30
TOKEN_SERIOUS_DAYS = 7


def _acs_client(name):
    client = services.find_client(name)
    reason = acs.unavailable_reason(client)
    if reason:
        raise ClientError(f"{name}: {reason}.")
    return client


# --- Estado y mantenimiento ----------------------------------------------------------------------

def status(client_name, force=False):
    """
    Estado del cliente: plataforma (tipo, ACS, token), estado del stack (experiencia, versión, reinicio pendiente),
    próximas ventanas de mantenimiento y change freezes. Lo que ACS no devuelva va en 'errors'.
    """
    client = services.find_client(client_name)
    result = {"client": client_name, "host": client.get("host", ""), "platform": services.platform_of(client, services.server_info(client_name)),
              "stack": None, "windows": [], "freezes": [], "preferred": None, "errors": {}}
    if not result["platform"]["acs"]:
        return result
    try:
        result["stack"] = services.stack_status(client_name, force=force)
        result["platform"] = services.platform_of(client, services.server_info(client_name))
    except acs.AcsError as e:
        result["errors"]["stack"] = str(e)
    try:
        result["windows"] = acs.maintenance_schedules(client)
    except acs.AcsError as e:
        result["errors"]["windows"] = str(e)
    try:
        prefs = acs.maintenance_preferences(client)
        result["freezes"] = prefs["freezes"]
        result["active_freezes"] = acs.active_freezes(prefs["freezes"])
        result["preferred"] = prefs["preferred"]
        if prefs.get("warning"):
            result["errors"]["preferences"] = prefs["warning"]
    except acs.AcsError as e:
        result["errors"]["freezes"] = str(e)
    return result


def overview(names=None):
    clients = services.list_clients()
    hosts = {c["name"]: c for c in clients}
    targets = [n for n in (names or list(hosts)) if n in hosts]

    def one(name):
        try:
            with capture_output():
                return status(name)
        except Exception as e:
            return {"client": name, "host": hosts[name]["host"], "platform": hosts[name].get("platform"), "stack": None,
                    "windows": [], "freezes": [], "errors": {}, "error": str(e)}

    with ThreadPoolExecutor(CLIENT_WORKERS) as pool:
        return list(pool.map(one, targets))


def build_restart(client_name, dry_run=False):
    """Tarea que reinicia el search head (o rolling restart del cluster) y espera a que vuelva. Devuelve (título, función)."""
    client = _acs_client(client_name)

    def run():
        print(f"▶ Solicitando el reinicio de {client_name} a ACS…")
        try:
            result = acs.restart(client, dry_run=dry_run)
        except acs.AcsError as e:
            print(f"❌ {e}")
            audit.record("stack.restart", "Reiniciar el stack", client_name, result="error")
            return
        if dry_run:
            print(f"[SIMULACIÓN] {result['request']['method']} {result['request']['url']}")
            audit.record("stack.restart", "Reiniciar el stack", client_name, dry_run=True)
            return
        audit.record("stack.restart", "Reiniciar el stack", client_name, {"message": result})
        print(f"ACS: {result}")
        services.disconnect(client_name)
        deadline = time.monotonic() + RESTART_TIMEOUT
        time.sleep(RESTART_POLL)
        while True:
            if cancel_requested():
                print("⏹ Se deja de esperar (el reinicio sigue en Splunk Cloud).")
                return
            try:
                state = services.stack_status(client_name, force=True)
                cluster = acs.restart_status(client)
                busy = any(m["rolling_restart"] or not m["ready"] for m in cluster)
                if state["status"].lower() == "ready" and not state["restart_required"] and not busy:
                    print(f"✅ {client_name} ha vuelto: estado {state['status']}, sin reinicio pendiente.")
                    return
                print(f"   … estado {state['status'] or '?'}{' (rolling restart en curso)' if busy else ''}; nueva comprobación en {RESTART_POLL} s")
            except acs.AcsError as e:
                print(f"   … todavía reiniciando ({str(e)[:120]}); nueva comprobación en {RESTART_POLL} s")
            if time.monotonic() >= deadline:
                print(f"⚠️ Tras {RESTART_TIMEOUT // 60} min el stack sigue sin responder como listo. Compruébelo en Splunk Cloud.")
                return
            time.sleep(RESTART_POLL)

    return f"Reiniciar {client_name}{' (simulación)' if dry_run else ''}", run


# --- Tokens de autenticación ---------------------------------------------------------------------

def token_level(expiry, now=None):
    """Nivel de aviso de la caducidad de un token: 'expired', 'serious' (< 7 días), 'warning' (< 30), 'ok' o None."""
    if not expiry:
        return None
    days = (expiry - (now or time.time())) / 86400
    if days < 0:
        return "expired"
    if days < TOKEN_SERIOUS_DAYS:
        return "serious"
    if days < TOKEN_WARN_DAYS:
        return "warning"
    return "ok"


def client_token(client):
    """Datos del token guardado del cliente (sin su valor): {present, expiry, level, user, audience, issued}."""
    token = client.get("splunkToken")
    claims = acs.token_claims(token)
    expiry = acs.token_expiry(token)
    return {"present": bool(token), "expiry": expiry, "level": token_level(expiry), "user": claims.get("sub") or "",
            "audience": claims.get("aud") or "", "issued": claims.get("iat"), "id": claims.get("jti") or ""}


def tokens(client_name):
    """Token del cliente y, si ACS está disponible, todos los tokens del stack."""
    client = services.find_client(client_name)
    result = {"client": client_name, "host": client.get("host", ""), "own": client_token(client),
              "platform": services.platform_of(client, services.server_info(client_name)), "tokens": [], "error": None,
              "has_password": bool(client.get("username") and client.get("password")), "username": client.get("username", "")}
    if result["platform"]["acs"]:
        try:
            rows = acs.list_tokens(client)
            for row in rows:
                row["level"] = token_level(row["expires_on"])
                row["is_own"] = bool(result["own"]["id"]) and row["id"] == result["own"]["id"]
            result["tokens"] = rows
        except acs.AcsError as e:
            result["error"] = str(e)
    return result


def tokens_overview(names=None, with_acs=False):
    """Caducidad del token guardado de cada cliente (sin red) y, con with_acs, los tokens del stack."""
    clients = services.list_clients()
    known = {c["name"]: c for c in clients}
    targets = [n for n in (names or list(known)) if n in known]

    def one(name):
        client = services.find_client(name)
        row = {"client": name, "host": client.get("host", ""), "own": client_token(client),
               "platform": services.platform_of(client, services.server_info(name)), "tokens": [], "error": None}
        if with_acs and row["platform"]["acs"]:
            try:
                with capture_output():
                    row["tokens"] = tokens(name)["tokens"]
            except Exception as e:
                row["error"] = str(e)
        return row

    with ThreadPoolExecutor(CLIENT_WORKERS) as pool:
        return list(pool.map(one, targets))


def create_token(client_name, data, dry_run=False):
    """
    Crea un token en el stack con usuario y contraseña (los del cliente si tiene, o los indicados) y, si se pide,
    lo guarda como token del cliente en config.py. Devuelve el token solo si no se guarda (ACS no lo vuelve a mostrar).
    """
    client = services.find_client(client_name)
    if not acs.stack_name(client.get("host")):
        raise ClientError(f"{client_name}: no es un stack de Splunk Cloud.")
    username = str(data.get("username") or client.get("username") or "").strip()
    password = str(data.get("password") or "") or (client.get("password") if not data.get("username") or data.get("username") == client.get("username") else "")
    if not (username and password):
        raise ClientError("Hacen falta el usuario y la contraseña del stack (se usan solo para esta petición).")
    user = str(data.get("user") or username).strip()
    audience = str(data.get("audience") or "Splunk Manager").strip()
    expires_on = str(data.get("expires_on") or "+90d").strip()
    try:
        result = acs.create_token(client, username, password, user, audience, expires_on, dry_run=dry_run)
    except acs.AcsError as e:
        audit.record("token.create", f"Crear token para {user}", client_name, {"audience": audience, "expires_on": expires_on}, result="error")
        raise ClientError(str(e))
    if dry_run:
        body = dict(result["request"]["body"])
        return {"dry_run": True, "message": f"[SIMULACIÓN] Se crearía un token para {user} ({audience}, caduca {expires_on}) en {client_name}",
                "request": {**result["request"], "body": body}}
    audit.record("token.create", f"Crear token para {user}", client_name,
                 {"audience": audience, "expires_on": expires_on, "id": result["id"], "saved": bool(data.get("save"))})
    response = {"id": result["id"], "user": result["user"], "audience": result["audience"], "expires_on": result["expires_on"],
                "saved": False, "token": ""}
    if data.get("save"):
        services.store_token(client_name, result["token"])
        response["saved"] = True
    else:
        response["token"] = result["token"]
    return response


def delete_token(client_name, token_id, dry_run=False):
    client = _acs_client(client_name)
    own = client_token(client)
    if own["id"] and own["id"] == token_id:
        raise ClientError("Ese es el token con el que Splunk Manager se conecta a este cliente: cree y guarde otro antes de borrarlo.")
    try:
        result = acs.delete_token(client, token_id, dry_run=dry_run)
    except acs.AcsError as e:
        audit.record("token.delete", f"Borrar token {token_id}", client_name, result="error")
        raise ClientError(str(e))
    audit.record("token.delete", f"Borrar token {token_id}", client_name, dry_run=dry_run)
    if dry_run:
        return {"dry_run": True, "message": f"[SIMULACIÓN] Se borraría el token {token_id} en {client_name}", "request": result["request"]}
    return {"ok": True}


# --- Exportar apps --------------------------------------------------------------------------------

def exports_dir(client):
    stack = acs.stack_name(client.get("host")) or SAFE.sub("_", client.get("name", "cliente"))
    return backup_dir(os.path.join(EXPORT_FOLDER, stack))


def list_exports(client_name):
    """Exportaciones guardadas del cliente: [{name, app, version, size, mtime}], las más recientes primero."""
    folder = exports_dir(services.find_client(client_name))
    files = []
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        if not (name.endswith(".tgz") and os.path.isfile(path)):
            continue
        match = re.match(r"^(?P<app>.+?)_(?P<stamp>\d{8}-\d{6})(?:_(?P<version>[^_]+))?\.tgz$", name)
        stat = os.stat(path)
        files.append({"name": name, "app": match.group("app") if match else name, "version": (match.group("version") or "") if match else "",
                      "size": stat.st_size, "mtime": stat.st_mtime})
    return sorted(files, key=lambda f: f["mtime"], reverse=True)


def export_path(client_name, filename):
    folder = exports_dir(services.find_client(client_name))
    if not filename or filename != os.path.basename(filename) or not filename.endswith(".tgz"):
        raise ClientError("Nombre de archivo no válido.")
    path = os.path.join(folder, filename)
    if not os.path.isfile(path):
        raise ClientError(f"No existe la exportación {filename}.")
    return path


def _prune(folder, app):
    files = sorted((f for f in os.listdir(folder) if f.startswith(f"{app}_") and f.endswith(".tgz")), reverse=True)
    for old in files[MAX_EXPORTS_PER_APP:]:
        try:
            os.remove(os.path.join(folder, old))
        except OSError:
            pass


def export_apps(client, names, options=None, log=print, versions=None, stop=None, progress=None):
    """
    Exporta cada app de 'names' a backups/apps/<stack>/<app>_<fecha>_<versión>.tgz. 'versions' es {app: versión} si se
    conoce. options: default, local, users, confs_only (bool) y keep_for_validation (guardar también en Validar app).
    Devuelve {"exported": [rutas], "failed": [apps]}.
    """
    options = options or {}
    folder = exports_dir(client)
    result = {"exported": [], "failed": [], "packages": []}
    total = len(names)
    for i, name in enumerate(names):
        if stop and stop():
            log("⏹ Cancelado.")
            break
        if progress:
            progress(i, total, name)
        started = time.time()
        try:
            data = acs.export_app(client, name, default=options.get("default", True), local=options.get("local", True),
                                  users=options.get("users", True), confs_only=options.get("confs_only", False))
        except acs.AcsError as e:
            log(f"❌ {name}: {e}")
            result["failed"].append(name)
            audit.record("app.export", f"Exportar app «{name}»", client.get("name"), result="error")
            continue
        version = SAFE.sub("_", str((versions or {}).get(name) or ""))
        filename = f"{SAFE.sub('_', name)}_{time.strftime('%Y%m%d-%H%M%S')}{f'_{version}' if version else ''}.tgz"
        path = os.path.join(folder, filename)
        with open(path, "wb") as f:
            f.write(data)
        _prune(folder, SAFE.sub("_", name))
        result["exported"].append(path)
        log(f"✅ {name}: {len(data) / 1024:.0f} KB en backups/{EXPORT_FOLDER}/{os.path.basename(folder)}/{filename} ({time.time() - started:.0f} s)")
        audit.record("app.export", f"Exportar app «{name}»", client.get("name"), {"file": filename, "bytes": len(data), "options": options})
        if options.get("keep_for_validation"):
            try:
                meta = app_packages.save_package(f"{name}_{client.get('name')}.tgz", data)
                result["packages"].append(meta.get("id"))
                log(f"   Guardada también en Validar app ({'ya existía' if meta.get('duplicate') else 'nueva versión'}).")
            except Exception as e:
                log(f"   ⚠️ No se pudo guardar en Validar app: {e}")
    if progress:
        progress(total, total, "")
    log(f"Resultado: {len(result['exported'])} apps exportadas, {len(result['failed'])} con error.")
    return result


def build_export(client_name, names, options):
    """Tarea de exportación de apps de un cliente. Devuelve (título, función)."""
    client = _acs_client(client_name)
    names = [str(n).strip() for n in (names or []) if str(n).strip()]
    if not names:
        raise ClientError("Seleccione al menos una app.")
    options = {k: bool((options or {}).get(k, k != "confs_only" and k != "keep_for_validation"))
               for k in ("default", "local", "users", "confs_only", "keep_for_validation")}
    label = f"«{names[0]}»" if len(names) == 1 else f"{len(names)} apps"

    def run():
        versions = {}
        try:
            versions = {a["name"]: a["version"] for a in acs.list_apps(client)}
        except acs.AcsError as e:
            print(f"⚠️ No se pudieron leer las versiones de las apps: {e}")
        export_apps(client, names, options, versions=versions, stop=cancel_requested, progress=set_progress)

    return f"Exportar {label} de {client_name}", run
