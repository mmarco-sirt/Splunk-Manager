import json
import os
import threading
import time

from core.paths import backup_dir

# Cada backup guarda además una copia con fecha en <carpeta>/historial/<nombre>/AAAAMMDD-HHMMSS.json
HISTORY_DIR = "historial"
MAX_VERSIONS = 30
VERSION_FORMAT = "%Y%m%d-%H%M%S"
# Campos que Splunk cambia solo (no son cambios reales de configuración)
VOLATILE_FIELDS = {"next_scheduled_time", "triggered_alert_count"}
# Backups con lo que había antes de cada edición (alertas, reportes, macros): <carpeta>_antes_de_editar_<host>_backup.json
EDIT_LABEL = "antes_de_editar"

_merge_lock = threading.Lock()


def without_volatile(data):
    """Copia de 'data' sin los campos volátiles (a cualquier nivel)."""
    if isinstance(data, dict):
        return {k: without_volatile(v) for k, v in data.items() if k not in VOLATILE_FIELDS}
    if isinstance(data, list):
        return [without_volatile(v) for v in data]
    return data


def history_dir(path):
    """Carpeta de historial de un archivo de backup."""
    folder, name = os.path.split(path)
    return os.path.join(folder, HISTORY_DIR, os.path.splitext(name)[0])


def list_versions(path):
    """Versiones guardadas de un backup, de la más reciente a la más antigua: [{id, time, size}]."""
    folder = history_dir(path)
    if not os.path.isdir(folder):
        return []
    versions = []
    for name in sorted(os.listdir(folder), reverse=True):
        if not name.endswith(".json"):
            continue
        try:
            stamp = time.mktime(time.strptime(name[:15], VERSION_FORMAT))
        except ValueError:
            continue
        versions.append({"id": name, "time": stamp, "size": os.path.getsize(os.path.join(folder, name))})
    return versions


def _same_content(path_a, path_b):
    try:
        with open(path_a, "r", encoding="utf-8") as a, open(path_b, "r", encoding="utf-8") as b:
            return without_volatile(json.load(a)) == without_volatile(json.load(b))
    except (OSError, ValueError):
        return False


def archive_version(path, timestamp=None):
    """
    Guarda una copia con fecha de 'path' en su historial, salvo que sea igual a la última versión.
    Conserva como máximo MAX_VERSIONS versiones. Devuelve el id de la versión creada o None.
    """
    folder = history_dir(path)
    os.makedirs(folder, exist_ok=True)
    existing = list_versions(path)
    if existing and _same_content(path, os.path.join(folder, existing[0]["id"])):
        return None

    stamp = time.strftime(VERSION_FORMAT, time.localtime(timestamp or time.time()))
    version_id = f"{stamp}.json"
    counter = 1
    while os.path.exists(os.path.join(folder, version_id)):
        version_id = f"{stamp}_{counter}.json"
        counter += 1
    with open(path, "rb") as src, open(os.path.join(folder, version_id), "wb") as dst:
        dst.write(src.read())

    for old in list_versions(path)[MAX_VERSIONS:]:
        os.remove(os.path.join(folder, old["id"]))
    return version_id


def write_backup(path, data):
    """Escribe un backup JSON y guarda la versión en su historial (también la anterior si aún no tenía)."""
    if os.path.exists(path) and not list_versions(path):
        archive_version(path, os.path.getmtime(path))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)
    archive_version(path)


def merge_backup(path, entries, key="name"):
    """
    Añade 'entries' al backup de 'path' (lista JSON), sustituyendo los elementos con el mismo 'key', y lo guarda con su
    historial. Lo usan los backups «antes de editar», que van acumulando lo que había antes de cada cambio.
    """
    with _merge_lock:
        existing = []
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            except (OSError, ValueError):
                existing = []
        keys = {e.get(key) for e in entries}
        kept = [e for e in existing if isinstance(e, dict) and e.get(key) not in keys] if isinstance(existing, list) else []
        write_backup(path, kept + entries)


def list_backup_files(folder, prefix=""):
    """Lista los archivos .json de backups/<folder> que empiezan por 'prefix'."""
    dir_path = backup_dir(folder)
    return sorted(f for f in os.listdir(dir_path) if f.startswith(prefix) and f.endswith(".json"))


def choose_backup_file(folder, prefix="", label="backup"):
    """Permite al usuario elegir por consola un archivo de backups/<folder>. Devuelve la ruta o None."""
    files = list_backup_files(folder, prefix)
    if not files:
        print(f"No hay archivos de {label} disponibles.")
        return None

    print(f"Seleccione un archivo de {label} para restaurar:")
    for i, file in enumerate(files, start=1):
        print(f"{i}. {file}")

    while True:
        choice = input("Ingrese el número del archivo (vacío para cancelar): ").strip()
        if not choice:
            print("Operación cancelada.")
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(files):
            return os.path.join(backup_dir(folder), files[int(choice) - 1])
        print("Selección no válida, intente nuevamente.")


def choose_multiple_backup_files(folder, prefix="", label="backup"):
    """Permite elegir varios archivos de backups/<folder> separados por coma. Devuelve lista de rutas."""
    files = list_backup_files(folder, prefix)
    if not files:
        print(f"No hay archivos de {label} disponibles.")
        return []

    print(f"\nSeleccione los archivos de {label}:")
    for i, file in enumerate(files, start=1):
        print(f"{i}) {file}")

    while True:
        selection = input("Ingrese los números separados por coma (ej: 1,3) (vacío para cancelar): ").strip()
        if not selection:
            print("Operación cancelada.")
            return []
        try:
            indexes = [int(x.strip()) - 1 for x in selection.split(",")]
            if all(0 <= idx < len(files) for idx in indexes):
                return [os.path.join(backup_dir(folder), files[idx]) for idx in indexes]
        except ValueError:
            pass
        print("Selección inválida. Intente nuevamente.")
