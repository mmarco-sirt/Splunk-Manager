import json
import os

BACKUP_DIR = "backups/roles"

def list_backup_files():
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)
        return []
    return [f for f in os.listdir(BACKUP_DIR) if f.startswith("roles_") and f.endswith(".json")]

def choose_backup_file():
    files = list_backup_files()
    if not files:
        print("No hay archivos de backup de roles disponibles.")
        return None

    print("Seleccione un archivo de backup de roles para restaurar:")
    for i, file in enumerate(files, start=1):
        print(f"{i}. {file}")

    while True:
        try:
            choice = int(input("Ingrese el número: ")) - 1
            if 0 <= choice < len(files):
                return f"{BACKUP_DIR}/{files[choice]}"
        except ValueError:
            print("Entrada no válida.")

def restore_roles(service):
    """Restaura roles desde un archivo JSON."""
    file_path = choose_backup_file()
    if not file_path:
        return

    try:
        with open(file_path, "r") as file:
            roles_data = json.load(file)

        for data in roles_data:
            role_name = data["name"]
            # Parámetros para la creación/actualización
            params = {
                "capabilities": data["capabilities"],
                "imported_roles": data["imported_roles"],
                "srchIndexesAllowed": data["srchIndexesAllowed"],
                "srchIndexesDefault": data["srchIndexesDefault"]
            }

            print(f"Procesando rol: {role_name}")

            if role_name in [r.name for r in service.roles]:
                existing_role = service.roles[role_name]
                existing_role.update(**params)
                print(f"Rol '{role_name}' actualizado.")
            else:
                service.roles.create(name=role_name, **params)
                print(f"Rol '{role_name}' creado.")

    except Exception as e:
        print(f"Error al restaurar roles: {e}")