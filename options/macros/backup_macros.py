import json
import os

BACKUP_DIR = "backups/macros"

def backup_macros(service):
    """Realiza un backup de las macros en un archivo JSON dentro de la carpeta 'backups/'."""
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)  # Crear la carpeta si no existe

    macros_backup = []

    # Descargar todas las macros configuradas
    for macro in service.macros:
        macros_backup.append({
            "name": macro.name,
            "definition": macro.definition,
            "args": macro.content.get("args", "")  # Guarda como cadena vacía si no tiene argumentos
        })

    # Guardar el backup en la carpeta 'backups/'
    file_name = f"{BACKUP_DIR}/macros_{service.host.split('.')[0]}_backup.json"
    with open(file_name, "w") as file:
        json.dump(macros_backup, file, indent=4)

    print(f"Backup completado: macros guardadas en {file_name}")
