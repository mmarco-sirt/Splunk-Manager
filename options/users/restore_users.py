import json

from core.backup_files import choose_backup_file

BACKUP_FOLDER = "users"

def restore_users(service, file_path=None):
    """
    Restaura usuarios desde un archivo JSON (si no se indica, se pide por consola).

    Acepta dos formatos por usuario:
      - Plantilla manual: username, password, roles, email, realname, [force-change-pass]
      - Salida de backup_users: username (o 'name' en backups antiguos), roles, email, realname (sin password)

    Los usuarios existentes se actualizan (la contraseña solo si viene en el archivo).
    Para crear usuarios nuevos sin contraseña en el archivo se pide una contraseña inicial
    y se fuerza su cambio en el primer login.
    """
    file_path = file_path or choose_backup_file(BACKUP_FOLDER, "users_", "backup de usuarios")
    if not file_path:
        return

    try:
        with open(file_path, "r", encoding="utf-8") as file:
            users = json.load(file)
    except Exception as e:
        print(f"Error al leer el archivo de usuarios: {e}")
        return

    existing_names = {u.name.lower() for u in service.users}
    initial_password = None  # Se pide solo una vez, si hace falta
    failed_users = []

    for user in users:
        username = user.get("username") or user.get("name")
        if not username:
            print(f"[ADVERTENCIA] Usuario sin 'username' en el archivo (email: {user.get('email', '-')}). Se omite.")
            failed_users.append(user.get("email") or "SIN_NOMBRE")
            continue

        params = {k: user[k] for k in ("roles", "email", "realname") if user.get(k)}
        if "force-change-pass" in user:
            params["force-change-pass"] = user["force-change-pass"]
        password = user.get("password")

        print(f"Restaurando usuario: {username}")

        try:
            if username.lower() in existing_names:
                if password:
                    params["password"] = password
                service.users[username.lower()].update(**params)
                print(f"Usuario '{username}' actualizado correctamente.")
            else:
                if not password:
                    if initial_password is None:
                        initial_password = input("Hay usuarios nuevos sin contraseña en el archivo. "
                                                 "Contraseña inicial (vacío para no crearlos): ")
                    if not initial_password:
                        print(f"Usuario '{username}' no creado: no tiene contraseña.")
                        failed_users.append(username)
                        continue
                    password = initial_password
                    params.setdefault("force-change-pass", True)

                roles = params.pop("roles", "user")
                service.users.create(username, password, roles, **params)
                existing_names.add(username.lower())
                print(f"Usuario '{username}' creado correctamente.")
        except Exception as e:
            print(f"Error al restaurar el usuario '{username}': {e}")
            failed_users.append(username)

    if failed_users:
        print("\n[ERROR] Los siguientes usuarios no se pudieron restaurar:")
        for name in failed_users:
            print(f" - {name}")
