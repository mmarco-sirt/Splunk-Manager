"""Operaciones de backup/restore lanzadas desde la web, reutilizando las funciones de options/."""
import difflib
import json
import os
import re
import shutil
import tempfile

from core.backup_files import history_dir, list_backup_files, list_versions, without_volatile, write_backup
from core.paths import backup_dir
from options.alerts.backup_alerts import (
    TECNOLOGIAS, backup_alerts, backup_alerts_especific, backup_alerts_custom, backup_alerts_by_name,
    backup_es_use_cases, backup_es_use_cases_custom,
)
from options.alerts.restore_alerts import KEEP_SHARING, restore_alerts_from_file
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
# Al restaurar alertas y reportes también se puede mantener la compartición que tenían
RESTORE_SHARING = (KEEP_SHARING,) + SHARING_LEVELS


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
        return "Backup de alertas CU-* y TEST - CU-*", backup_alerts
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
    if sharing not in RESTORE_SHARING:
        raise OperationError("Nivel de compartición no válido.")
    opts = {"sharing": sharing, "initial_password": options.get("initial_password") or ""}

    items = [str(i) for i in options.get("items") or []]
    if items and (len(paths) != 1 or not BACKUP_FOLDERS[folder]["key"]):
        raise OperationError("Solo se pueden elegir elementos concretos al restaurar un único archivo.")
    version = options.get("version")
    if version and len(paths) != 1:
        raise OperationError("Solo se puede restaurar una versión anterior de un único archivo.")
    source = version_path(folder, files[0], version) if version else None

    restore = BACKUP_FOLDERS[folder]["restore"]
    label = BACKUP_FOLDERS[folder]["label"].lower()

    def run(service):
        for path in paths:
            suffix = f" (versión del {version_label(version)})" if version else ""
            print(f"\n▶ Restaurando {os.path.basename(path)}{suffix}")
            try:
                if items or source:
                    _restore_copy(service, folder, source or path, os.path.basename(path), items, restore, opts)
                else:
                    restore(service, path, opts)
            except Exception as e:
                print(f"❌ Error en la restauración de {os.path.basename(path)}: {e}")

    title = f"Restore de {label}: {files[0]}" if len(files) == 1 else f"Restore de {len(files)} archivos de {label}"
    if version:
        title += f" · versión del {version_label(version)}"
    if items:
        title += f" ({len(items)} elementos)"
    return title, run


def _item_name(entry, key):
    return entry.get(key) or entry.get("name") if isinstance(entry, dict) else None


def _restore_copy(service, folder, source, original_name, items, restore, opts):
    """
    Restaura desde una copia temporal con el nombre original del archivo (el restore de dashboards saca el id
    del nombre). Sirve para restaurar una versión del historial y/o solo algunos elementos.
    """
    with open(source, "r", encoding="utf-8") as f:
        data = json.load(f)
    if items:
        key = BACKUP_FOLDERS[folder]["key"]
        wanted = set(items)
        selected = [entry for entry in data if _item_name(entry, key) in wanted]
        print(f"Elementos seleccionados: {len(selected)} de {len(data)}")
        data = selected

    temp_dir = tempfile.mkdtemp(prefix="splunk_manager_")
    try:
        temp_path = os.path.join(temp_dir, original_name)
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        restore(service, temp_path, opts)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# --- Historial de versiones ---

def version_label(version_id):
    """'20261007-170512.json' -> '07/10/2026 17:05:12'"""
    stamp = version_id[:15]
    return f"{stamp[6:8]}/{stamp[4:6]}/{stamp[0:4]} {stamp[9:11]}:{stamp[11:13]}:{stamp[13:15]}"


def version_path(folder, filename, version_id):
    """Ruta segura a una versión del historial de backups/<folder>/<filename>."""
    path = backup_path(folder, filename)
    if not version_id or version_id != os.path.basename(version_id) or not version_id.endswith(".json"):
        raise OperationError("Versión no válida.")
    full = os.path.join(history_dir(path), version_id)
    if not os.path.isfile(full):
        raise OperationError(f"No existe la versión {version_id}.")
    return full


def backup_versions(folder, filename):
    """Versiones del historial de un backup con su nº de elementos, de la más reciente a la más antigua."""
    path = backup_path(folder, filename)
    versions = list_versions(path)
    for v in versions:
        v["items"] = _count_items(os.path.join(history_dir(path), v["id"]))
    return versions


def _load(folder, filename, version_id=None):
    if version_id in (None, "", "current"):
        path = backup_path(folder, filename)
    else:
        path = version_path(folder, filename, version_id)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _flatten(value, prefix=""):
    """Aplana diccionarios para comparar campo a campo ({'content': {'search': x}} -> {'search': x})."""
    if not isinstance(value, dict):
        return {prefix or "valor": value}
    flat = {}
    for k, v in value.items():
        name = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            flat.update(_flatten(v, "" if k == "content" and not prefix else name))
        else:
            flat[name] = v
    return flat


def _as_text(value):
    if value is None:
        return ""
    if isinstance(value, list):
        return "\n".join(str(v) for v in value)
    return str(value)


_MISSING = object()
MAX_DIFF_LINES = 400


def diff_fields(old, new):
    """Diferencias campo a campo entre dos elementos: [{field, before, after, diff}] (None = no existía)."""
    old_flat, new_flat = _flatten(old), _flatten(new)
    fields = []
    for field in sorted(set(old_flat) | set(new_flat)):
        before, after = old_flat.get(field, _MISSING), new_flat.get(field, _MISSING)
        if before == after:
            continue
        before_text = "" if before is _MISSING else _as_text(before)
        after_text = "" if after is _MISSING else _as_text(after)
        diff = None
        if "\n" in before_text + after_text or max(len(before_text), len(after_text)) > 80:
            lines = difflib.unified_diff(before_text.splitlines(), after_text.splitlines(), lineterm="", n=2)
            diff = list(lines)[2:MAX_DIFF_LINES + 2]  # sin las cabeceras ---/+++
        fields.append({
            "field": field,
            "before": None if before is _MISSING else before_text,
            "after": None if after is _MISSING else after_text,
            "diff": diff,
        })
    return fields


def diff_backup(folder, filename, old_id, new_id):
    """Compara dos versiones de un backup ('current' es el archivo actual), ignorando los campos volátiles."""
    old = without_volatile(_load(folder, filename, old_id))
    new = without_volatile(_load(folder, filename, new_id))
    key = BACKUP_FOLDERS[folder]["key"]
    if key and isinstance(old, list) and isinstance(new, list):
        old_map = {_item_name(e, key) or f"#{i}": e for i, e in enumerate(old)}
        new_map = {_item_name(e, key) or f"#{i}": e for i, e in enumerate(new)}
    else:
        old_map, new_map = {filename: old}, {filename: new}

    common = set(old_map) & set(new_map)
    changed = []
    for name in sorted(common):
        fields = diff_fields(old_map[name], new_map[name])
        if fields:
            changed.append({"name": name, "fields": fields})
    return {
        "from": old_id,
        "to": new_id,
        "added": sorted(set(new_map) - set(old_map)),
        "removed": sorted(set(old_map) - set(new_map)),
        "changed": changed,
        "unchanged": len(common) - len(changed),
    }


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
            files.append({"name": name, "size": stat.st_size, "mtime": stat.st_mtime, "items": _count_items(path),
                          "versions": len(list_versions(path))})
        result[folder] = {"label": meta["label"], "prefix": meta["prefix"], "files": files}
    return result


def _count_items(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return len(data) if isinstance(data, list) else 1
    except (OSError, ValueError):
        return None


def read_backup(folder, filename, version_id=None):
    """Contenido de un backup (o de una versión de su historial) y los nombres de sus elementos."""
    data = _load(folder, filename, version_id)
    key = BACKUP_FOLDERS[folder]["key"]
    names = [_item_name(e, key) for e in data] if key and isinstance(data, list) else []
    return {"folder": folder, "name": filename, "version": version_id, "data": data, "items": [n for n in names if n]}


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

    write_backup(backup_path(folder, filename), data)
    return filename
