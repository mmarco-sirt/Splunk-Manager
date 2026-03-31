import json
import os

BACKUP_DIR = "backups/roles"

def backup_roles(service):
    """Realiza un backup de los roles en un archivo JSON."""
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)

    roles_backup = []

    # Acceder a los roles a través de service.roles
    for role in service.roles:
        roles_backup.append({
            "name": role.name,
            "capabilities": role.content.get("capabilities", []),
            "imported_roles": role.content.get("imported_roles", []),
            "srchIndexesAllowed": role.content.get("srchIndexesAllowed", []),
            "srchIndexesDefault": role.content.get("srchIndexesDefault", [])
        })

    file_name = f"{BACKUP_DIR}/roles_{service.host.split('.')[0]}_backup.json"
    with open(file_name, "w") as file:
        json.dump(roles_backup, file, indent=4)

    print(f"Backup completado: {len(roles_backup)} roles guardados en {file_name}")