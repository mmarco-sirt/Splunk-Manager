import json
import os

from core.paths import backup_dir, host_tag

BACKUP_FOLDER = "users"

def backup_users(service):
    """Realiza un backup de los usuarios de Splunk en un archivo JSON dentro de 'backups/users/'.
    Splunk no expone las contraseñas, por lo que el backup no las incluye."""
    users_backup = []
    for user in service.users:
        users_backup.append({
            "username": user.name,
            "roles": user.roles,
            "email": user.content.get("email", ""),
            "realname": user.content.get("realname", "")
        })

    file_name = os.path.join(backup_dir(BACKUP_FOLDER), f"users_{host_tag(service)}_backup.json")
    with open(file_name, "w", encoding="utf-8") as file:
        json.dump(users_backup, file, indent=4)

    print(f"Backup completado: {len(users_backup)} usuarios guardados en {file_name}")
