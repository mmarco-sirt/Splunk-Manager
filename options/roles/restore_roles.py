import json

from core.backup_files import choose_backup_file

BACKUP_FOLDER = "roles"
ROLE_FIELDS = ("capabilities", "imported_roles", "srchIndexesAllowed", "srchIndexesDefault")

def restore_roles(service, file_path=None):
    """Restaura roles desde un archivo JSON (si no se indica, se pide por consola)."""
    file_path = file_path or choose_backup_file(BACKUP_FOLDER, "roles_", "backup de roles")
    if not file_path:
        return

    try:
        with open(file_path, "r", encoding="utf-8") as file:
            roles_data = json.load(file)

        existing_names = {r.name for r in service.roles}

        for data in roles_data:
            role_name = data["name"]
            # Parámetros para la creación/actualización (solo los presentes en el backup)
            params = {k: data[k] for k in ROLE_FIELDS if k in data}

            print(f"Procesando rol: {role_name}")

            try:
                if role_name in existing_names:
                    service.roles[role_name].update(**params)
                    print(f"Rol '{role_name}' actualizado.")
                else:
                    service.roles.create(role_name, **params)
                    existing_names.add(role_name)
                    print(f"Rol '{role_name}' creado.")
            except Exception as e:
                print(f"Error al restaurar el rol '{role_name}': {e}")

    except Exception as e:
        print(f"Error al restaurar roles: {e}")
