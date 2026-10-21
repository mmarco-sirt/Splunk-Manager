import os

from core.backup_files import EDIT_LABEL, merge_backup, write_backup
from core.paths import backup_dir, host_tag

BACKUP_FOLDER = "macros"
# Además de nombre, definición y argumentos se guardan estos campos (si tienen valor); el restore los aplica si están
EXTRA_FIELDS = ("iseval", "disabled", "description", "validation", "errormsg")


def macro_entry(macro):
    """Elemento de backup de una macro."""
    content = macro.content
    entry = {
        "name": macro.name,
        "definition": content.get("definition", ""),
        "args": content.get("args", "")  # Guarda como cadena vacía si no tiene argumentos
    }
    for field in EXTRA_FIELDS:
        value = content.get(field)
        if value not in (None, ""):
            entry[field] = value
    return entry


def backup_macros(service):
    """Realiza un backup de las macros en un archivo JSON dentro de 'backups/macros/'."""
    # Descargar todas las macros configuradas
    macros_backup = [macro_entry(macro) for macro in service.macros]

    file_name = os.path.join(backup_dir(BACKUP_FOLDER), f"macros_{host_tag(service)}_backup.json")
    write_backup(file_name, macros_backup)

    print(f"Backup completado: {len(macros_backup)} macros guardadas en {file_name}")


def backup_before_edit(service, macros):
    """
    Guarda la configuración actual de esas macros en backups/macros/macros_antes_de_editar_<host>_backup.json (con
    historial), sustituyendo las entradas anteriores con el mismo nombre. Devuelve la ruta del archivo.
    """
    path = os.path.join(backup_dir(BACKUP_FOLDER), f"macros_{EDIT_LABEL}_{host_tag(service)}_backup.json")
    merge_backup(path, [macro_entry(m) for m in macros])
    return path
