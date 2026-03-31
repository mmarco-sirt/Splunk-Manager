import json
import os

BACKUP_DIR = "backups/users"

def list_backup_files():
    """Lista los archivos de backup disponibles en la carpeta 'backups/users/'."""
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)
        return []
    
    files = [f for f in os.listdir(BACKUP_DIR) if f.startswith(f"users_") and f.endswith(".json")]
    return files

def choose_backup_file():
    """Permite al usuario elegir un archivo de backup de usuarios para restaurar."""
    files = list_backup_files()
    
    if not files:
        print("No hay archivos de backup de usuarios disponibles.")
        return None

    print("Seleccione un archivo de backup de usuarios para restaurar:")
    for i, file in enumerate(files, start=1):
        print(f"{i}. {file}")

    while True:
        try:
            choice = int(input("Ingrese el número del archivo: ")) - 1
            if 0 <= choice < len(files):
                return f"{BACKUP_DIR}/{files[choice]}"
            print("Número fuera de rango, intente nuevamente.")
        except ValueError:
            print("Entrada no válida, ingrese un número.")

def restore_users(service):
    """Restaura usuarios desde un archivo JSON seleccionado por el usuario."""
    file_path = choose_backup_file()
    if not file_path:
        return
    
    try:
        with open(file_path, "r") as file:
            users = json.load(file)

        for user in users:
            roles = user["roles"]
            email = user["email"]
            realname = user["realname"]
            username = user["username"]
            password = user["password"]
            #forcechangepass  = user["force-change-pass"]

            print(f"Restaurando usuario: {username}")

            if username in [u.name for u in service.users]:
                existing_user = service.users[username]
                existing_user.update(roles=roles, email=email, realname=realname, username=username, password=password) 
                print(f"Usuario '{username}' actualizado correctamente.")
            else:
                service.users.create(roles=roles, email=email, realname=realname, username=username, password=password) 
                print(f"Usuario '{username}' creado correctamente.")
    
    except Exception as e:
        print(f"Error al restaurar usuarios: {e}")

def restore_users2(service):
    """Restaura usuarios desde un archivo JSON seleccionado por el usuario."""
    file_path = choose_backup_file()
    if not file_path:
        return
    
    try:
        with open(file_path, "r") as file:
            users = json.load(file)

        for user in users:
            print(f"Restaurando usuario: {user['username']}")

            if user["username"] in [u.name for u in service.users]:
                existing_user = service.users[user["username"]]
                existing_user.update(**user)  # Pasamos todos los valores del diccionario directamente
                print(f"Usuario '{user['username']}' actualizado correctamente.")
            else:
                service.users.create(**user)  # Pasamos el diccionario completo
                print(f"Usuario '{user['username']}' creado correctamente.")
    
    except Exception as e:
        print(f"Error al restaurar usuarios: {e}")