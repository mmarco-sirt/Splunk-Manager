import json
import os

from core.paths import backup_dir, host_tag

BACKUP_FOLDER = "roles"

def backup_roles(service):
    """Realiza un backup de los roles en un archivo JSON dentro de 'backups/roles/'."""
    roles_backup = []

    for role in service.roles:
        roles_backup.append({
            "name": role.name,
            "capabilities": role.content.get("capabilities", []),
            "imported_roles": role.content.get("imported_roles", []),
            "srchIndexesAllowed": role.content.get("srchIndexesAllowed", []),
            "srchIndexesDefault": role.content.get("srchIndexesDefault", [])
        })

    file_name = os.path.join(backup_dir(BACKUP_FOLDER), f"roles_{host_tag(service)}_backup.json")
    with open(file_name, "w", encoding="utf-8") as file:
        json.dump(roles_backup, file, indent=4)

    print(f"Backup completado: {len(roles_backup)} roles guardados en {file_name}")
