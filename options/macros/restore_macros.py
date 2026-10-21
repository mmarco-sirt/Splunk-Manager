import json

from core.backup_files import choose_backup_file
from options.macros.backup_macros import EXTRA_FIELDS

BACKUP_FOLDER = "macros"

def restore_macros(service, file_path=None):
    """Restaura macros desde un archivo JSON (si no se indica, se pide por consola) y las hace globales."""
    file_path = file_path or choose_backup_file(BACKUP_FOLDER, "macros_", "backup de macros")
    if not file_path:
        return

    try:
        with open(file_path, "r", encoding="utf-8") as file:
            macros = json.load(file)

        existing_names = {m.name for m in service.macros}

        for macro in macros:
            macro_name = macro["name"]
            macro_definition = macro["definition"]
            macro_args = macro.get("args", "")
            macro_sharing = "global"
            # Backups nuevos: eval, estado, descripción y validación (los antiguos no los traen y no se tocan)
            extra = {k: macro[k] for k in EXTRA_FIELDS if k in macro}

            print(f"Restaurando macro: {macro_name}")

            try:
                if macro_name in existing_names:
                    existing_macro = service.macros[macro_name]
                    existing_macro.update(definition=macro_definition, args=macro_args, sharing=macro_sharing, **extra)
                    print(f"Macro '{macro_name}' actualizada correctamente.")
                else:
                    service.macros.create(name=macro_name, definition=macro_definition, args=macro_args, sharing=macro_sharing, **extra)
                    existing_names.add(macro_name)
                    print(f"Macro '{macro_name}' creada correctamente.")
            except Exception as e:
                print(f"Error al restaurar la macro '{macro_name}': {e}")

    except Exception as e:
        print(f"Error al restaurar macros: {e}")
