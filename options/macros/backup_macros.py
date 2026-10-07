import json
import os

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
    with open(file_name, "w", encoding="utf-8") as file:
        json.dump(macros_backup, file, indent=4)

    print(f"Backup completado: {len(macros_backup)} macros guardadas en {file_name}")
