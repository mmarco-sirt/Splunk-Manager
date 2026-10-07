import os

from core.backup_files import write_backup
from core.paths import backup_dir, host_tag

BACKUP_FOLDER = "macros"

def backup_macros(service):
    """Realiza un backup de las macros en un archivo JSON dentro de 'backups/macros/'."""
    macros_backup = []

    # Descargar todas las macros configuradas
    for macro in service.macros:
        macros_backup.append({
            "name": macro.name,
            "definition": macro.definition,
            "args": macro.content.get("args", "")  # Guarda como cadena vacía si no tiene argumentos
        })

    file_name = os.path.join(backup_dir(BACKUP_FOLDER), f"macros_{host_tag(service)}_backup.json")
    write_backup(file_name, macros_backup)

    print(f"Backup completado: {len(macros_backup)} macros guardadas en {file_name}")
