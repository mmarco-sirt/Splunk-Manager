"""Operaciones de backup/restore lanzadas desde la web, reutilizando las funciones de options/."""
import json
import os
import re
import shutil
import tempfile

from core.backup_files import list_backup_files
from core.paths import backup_dir
from options.alerts.backup_alerts import (
    TECNOLOGIAS, backup_alerts, backup_alerts_especific, backup_alerts_custom, backup_alerts_by_name,
    backup_es_use_cases, backup_es_use_cases_custom,
)
from options.alerts.restore_alerts import restore_alerts_from_file
from options.dashboards.backup_dashboard import backup_dashboard
from options.dashboards.restore_dashboard import restore_dashboard
from options.macros.backup_macros import backup_macros
from options.macros.restore_macros import restore_macros
from options.reports.backup_reports import backup_reports
from options.roles.backup_roles import backup_roles
from options.roles.restore_roles import restore_roles
from options.users.backup_users import backup_users
from options.users.restore_users import restore_users
from web.services import ClientError, get_service

INVALID_LABEL = re.compile(r'[\\/:*?"<>|]')
SHARING_LEVELS = ("global", "app", "user")


class OperationError(Exception):
    """Parámetros inválidos para una operación."""


# Carpetas de backups/ que entiende la web: prefijo de archivo, clave de nombre y función de restore
BACKUP_FOLDERS = {
    "alertas": {"label": "Alertas", "prefix": "alertas_", "key": "name",
                "restore": lambda svc, path, opts: restore_alerts_from_file(svc, path, sharing=opts["sharing"])},
    "reportes": {"label": "Reportes", "prefix": "reportes_", "key": "name",
                 "restore": lambda svc, path, opts: restore_alerts_from_file(svc, path, sharing=opts["sharing"])},
    "macros": {"label": "Macros", "prefix": "macros_", "key": "name",
               "restore": lambda svc, path, opts: restore_macros(svc, path)},
    "users": {"label": "Usuarios", "prefix": "users_", "key": "username",
              "restore": lambda svc, path, opts: restore_users(svc, path, initial_password=opts["initial_password"])},
    "roles": {"label": "Roles", "prefix": "roles_", "key": "name",
              "restore": lambda svc, path, opts: restore_roles(svc, path)},
    "dashboards": {"label": "Dashboards", "prefix": "", "key": None,
                   "restore": lambda svc, path, opts: restore_dashboard(svc, path)},
}


def _text_option(options, key, what):
    value = str(options.get(key) or "").strip()
    if not value:
        raise OperationError(f"Falta {what}.")
    if INVALID_LABEL.search(value):
        raise OperationError(f"{what.capitalize()} no puede contener \\ / : * ? \" < > |")
    return value


def _names_option(options):
    names = [str(n).strip() for n in options.get("names") or [] if str(n).strip()]
    if not names:
        raise OperationError("No se ha seleccionado ningún elemento.")
    return names


def _backup_dashboards(service, names):
    for name in names:
        backup_dashboard(service, name)


def build_backup(kind, options):
    """Devuelve (título, función(service)) para el tipo de backup pedido, validando las opciones."""
    if kind == "alerts_all":
        return "Backup de alertas CU-*", backup_alerts
    if kind == "alerts_tech":
        tech = options.get("tech")
        if tech not in TECNOLOGIAS:
            raise OperationError("Tecnología no válida.")
        return f"Backup de alertas {tech}", lambda svc: backup_alerts_especific(svc, tech)
    if kind == "alerts_custom":
        text = _text_option(options, "text", "el texto a buscar")
        return f"Backup de alertas «{text}»", lambda svc: backup_alerts_custom(svc, text)
    if kind == "es_all":
        return "Backup de Enterprise Security", backup_es_use_cases
    if kind == "es_custom":
        text = _text_option(options, "text", "el texto a buscar")
        return f"Backup de ES «{text}»", lambda svc: backup_es_use_cases_custom(svc, text)
    if kind == "alerts_selected":
        names = _names_option(options)
        label = _text_option(options, "label", "el nombre del backup") if options.get("label") else "seleccion"
        return f"Backup de {len(names)} alertas", lambda svc: backup_alerts_by_name(svc, names, label)
    if kind == "dashboards":
        names = _names_option(options)
        return f"Backup de {len(names)} dashboards", lambda svc: _backup_dashboards(svc, names)
    simple = {
        "macros": ("Backup de macros", backup_macros),
        "users": ("Backup de usuarios", backup_users),
        "roles": ("Backup de roles", backup_roles),
        "reports": ("Backup de reportes", backup_reports),
    }
    if kind in simple:
        return simple[kind]
    raise OperationError(f"Tipo de backup desconocido: {kind}")


def backup_path(folder, filename):
    """Ruta segura a backups/<folder>/<filename>; impide salir de la carpeta."""
    if folder not in BACKUP_FOLDERS:
        raise OperationError(f"Carpeta de backups desconocida: {folder}")
    if not filename or filename != os.path.basename(filename) or not filename.endswith(".json"):
        raise OperationError("Nombre de archivo no válido.")
    return os.path.join(backup_dir(folder), filename)


def build_restore(folder, files, options):
    """Devuelve (título, función(service)) para restaurar uno o varios archivos de backups/<folder>."""
    if not files:
        raise OperationError("No se ha seleccionado ningún archivo.")
    paths = [backup_path(folder, f) for f in files]
    for path in paths:
        if not os.path.isfile(path):
            raise OperationError(f"No existe el archivo {os.path.basename(path)}.")

    sharing = options.get("sharing") or "global"
    if sharing not in SHARING_LEVELS:
        raise OperationError("Nivel de compartición no válido.")
    opts = {"sharing": sharing, "initial_password": options.get("initial_password") or ""}

    items = [str(i) for i in options.get("items") or []]
    if items and (len(paths) != 1 or not BACKUP_FOLDERS[folder]["key"]):
        raise OperationError("Solo se pueden elegir elementos concretos al restaurar un único archivo.")

    restore = BACKUP_FOLDERS[folder]["restore"]
    label = BACKUP_FOLDERS[folder]["label"].lower()

    def run(service):
        for path in paths:
            print(f"\n▶ Restaurando {os.path.basename(path)}")
            try:
                if items:
                    _restore_items(service, folder, path, items, restore, opts)
                else:
                    restore(service, path, opts)
            except Exception as e:
                print(f"❌ Error en la restauración de {os.path.basename(path)}: {e}")

    title = f"Restore de {label}: {files[0]}" if len(files) == 1 else f"Restore de {len(files)} archivos de {label}"
    if items:
        title += f" ({len(items)} elementos)"
    return title, run


def _item_name(entry, key):
    return entry.get(key) or entry.get("name") if isinstance(entry, dict) else None


def _restore_items(service, folder, path, items, restore, opts):
    """Restaura solo los elementos indicados, mediante una copia filtrada del archivo con el mismo nombre."""
    key = BACKUP_FOLDERS[folder]["key"]
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    wanted = set(items)
    selected = [entry for entry in data if _item_name(entry, key) in wanted]
    print(f"Elementos seleccionados: {len(selected)} de {len(data)}")

    temp_dir = tempfile.mkdtemp(prefix="splunk_manager_")
    try:
        temp_path = os.path.join(temp_dir, os.path.basename(path))
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(selected, f, indent=4)
        restore(service, temp_path, opts)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def run_for_clients(clients, action):
    """Ejecuta action(service) en cada cliente, conectando si hace falta. Pensado para correr dentro de una tarea."""
    def run():
        for name in clients:
            if len(clients) > 1:
                print(f"\n━━━━━━━━━━ {name} ━━━━━━━━━━")
            try:
                service = get_service(name)
            except ClientError as e:
                print(f"❌ {e}")
                continue
            try:
                action(service)
            except Exception as e:
                print(f"❌ Error en {name}: {e}")
    return run


def describe_backups():
    """Inventario de backups/ por carpeta: archivo, tamaño, fecha y nº de elementos."""
    result = {}
    for folder, meta in BACKUP_FOLDERS.items():
        files = []
        for name in list_backup_files(folder, meta["prefix"]):
            path = os.path.join(backup_dir(folder), name)
            stat = os.stat(path)
            files.append({"name": name, "size": stat.st_size, "mtime": stat.st_mtime, "items": _count_items(path)})
        result[folder] = {"label": meta["label"], "prefix": meta["prefix"], "files": files}
    return result


def _count_items(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return len(data) if isinstance(data, list) else 1
    except (OSError, ValueError):
        return None


def read_backup(folder, filename):
    """Contenido de un backup y la lista de nombres de sus elementos (para elegir qué restaurar)."""
    path = backup_path(folder, filename)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    key = BACKUP_FOLDERS[folder]["key"]
    names = [_item_name(e, key) for e in data] if key and isinstance(data, list) else []
    return {"folder": folder, "name": filename, "data": data, "items": [n for n in names if n]}


def save_uploaded_backup(folder, filename, content):
    """Guarda un JSON subido desde el navegador en backups/<folder>/, añadiendo el prefijo si falta."""
    if folder not in BACKUP_FOLDERS:
        raise OperationError(f"Carpeta de backups desconocida: {folder}")
    filename = os.path.basename(str(filename or "").strip())
    if not filename.endswith(".json"):
        raise OperationError("El archivo debe ser .json.")
    if INVALID_LABEL.search(filename):
        raise OperationError("Nombre de archivo no válido.")
    prefix = BACKUP_FOLDERS[folder]["prefix"]
    if prefix and not filename.startswith(prefix):
        filename = prefix + filename
    if folder == "dashboards" and not filename.endswith("_backup.json"):
        raise OperationError("Los backups de dashboards deben llamarse <id_dashboard>_backup.json.")

    try:
        data = json.loads(content)
    except ValueError as e:
        raise OperationError(f"El archivo no es un JSON válido: {e}")
    if folder == "dashboards" and not (isinstance(data, dict) and "eai:data" in data):
        raise OperationError("Un backup de dashboard debe ser un objeto JSON con el campo 'eai:data'.")
    if folder != "dashboards" and not isinstance(data, list):
        raise OperationError("El backup debe ser una lista JSON de elementos.")

    path = backup_path(folder, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)
    return filename
