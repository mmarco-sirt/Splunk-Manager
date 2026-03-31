import json
import os

BACKUP_DIR = "backups/users"

def backup_users(service):
    """Realiza un backup de los usuarios de Splunk en un archivo JSON dentro de 'backups/users/'."""
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)
    
    users_backup = []
    for user in service.users:
        users_backup.append({
            "name": user.name,
            "roles": user.roles,
            "email": user.content.get("email", ""),
            "realname": user.content.get("realname", "")
        })
    
    file_name = f"{BACKUP_DIR}/users_{service.host.split('.')[0]}_backup.json"
    with open(file_name, "w") as file:
        json.dump(users_backup, file, indent=4)
    
    print(f"Backup completado: usuarios guardados en {file_name}")
