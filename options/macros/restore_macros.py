import json
import os
import urllib.parse

BACKUP_DIR = "backups/macros"

def list_backup_files():
    """Lista los archivos de backup disponibles en la carpeta 'backups/'."""
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)  # Crear la carpeta si no existe
        return []

    files = [f for f in os.listdir(BACKUP_DIR) if f.startswith("macros_") and f.endswith(".json")]
    return files

def choose_backup_file():
    """Permite al usuario elegir un archivo de backup de macros para restaurar."""
    files = list_backup_files()
    
    if not files:
        print("No hay archivos de backup de macros disponibles.")
        return None

    print("Seleccione un archivo de backup de macros para restaurar:")
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

def restore_macros(service):
    """Restaura macros desde un archivo JSON seleccionado por el usuario y las hace globales."""
    file_path = choose_backup_file()
    if not file_path:
        return

    try:
        with open(file_path, "r") as file:
            macros = json.load(file)

        for macro in macros:
            macro_name = macro["name"]
            macro_definition = macro["definition"]
            macro_args = macro["args"]
            macro_sharing = "global"

            print(f"Restaurando macro: {macro_name}")

            if macro_name in [m.name for m in service.macros]:
                existing_macro = service.macros[macro_name]
                existing_macro.update(definition=macro_definition, args=macro_args, sharing=macro_sharing)
                print(f"Macro '{macro_name}' actualizada correctamente.")
            else:
                new_macro = service.macros.create(name=macro_name, definition=macro_definition, args=macro_args, sharing=macro_sharing)
                print(f"Macro '{macro_name}' creada correctamente.")

    except Exception as e:
        print(f"Error al restaurar macros: {e}")

#def restore_macros(service):
    """Restaura macros desde un archivo JSON seleccionado por el usuario."""
    file_path = choose_backup_file()
    if not file_path:
        return

    try:
        with open(file_path, "r") as file:
            macros = json.load(file)

        for macro in macros:
            macro_name = macro["name"]
            macro_definition = macro["definition"]
            macro_args = macro["args"]
            #macro_args = macro.get("args", "")  # Se mantiene como string

            print(f"Restaurando macro: {macro_name}")

            if macro_name in [m.name for m in service.macros]:
                existing_macro = service.macros[macro_name]
                existing_macro.update(definition=macro_definition, args=macro_args)
                print(f"Macro '{macro_name}' actualizada correctamente.")
            else:
                service.macros.create(name=macro_name, definition=macro_definition, args=macro_args)
                print(f"Macro '{macro_name}' creada correctamente.")

    except Exception as e:
        print(f"Error al restaurar macros: {e}")