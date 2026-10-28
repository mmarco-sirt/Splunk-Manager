import json

from core.backup_files import choose_backup_file
from core.knowledge import all_macros, apply_sharing, pick_existing
from options.macros.backup_macros import EXTRA_FIELDS

BACKUP_FOLDER = "macros"

def restore_macros(service, file_path=None):
    """
    Restaura macros desde un archivo JSON (si no se indica, se pide por consola). Si ya existe una macro con ese nombre
    (en cualquier app o usuario) se actualiza esa misma, en su app: la de la app y el propietario que guarda el backup
    ('acl', y conserva su compartición) o, en los backups antiguos sin 'acl', la compartida, que se hace global como
    antes. Las que no existen se crean globales.
    """
    file_path = file_path or choose_backup_file(BACKUP_FOLDER, "macros_", "backup de macros")
    if not file_path:
        return

    try:
        with open(file_path, "r", encoding="utf-8") as file:
            macros = json.load(file)

        existing = {}
        for m in all_macros(service):
            existing.setdefault(m.name, []).append(m)
        user = getattr(service, "username", None)

        for macro in macros:
            macro_name = macro["name"]
            macro_definition = macro["definition"]
            macro_args = macro.get("args", "")
            macro_sharing = "global"
            # Backups nuevos: eval, estado, descripción y validación (los antiguos no los traen y no se tocan)
            extra = {k: macro[k] for k in EXTRA_FIELDS if k in macro}

            print(f"Restaurando macro: {macro_name}")

            try:
                target = pick_existing(existing.get(macro_name, []), macro.get("acl"), user)
                if target is not None:
                    # Sin 'sharing' en update(): el SDK lo tomaría como namespace y escribiría en nobody/system
                    target.update(definition=macro_definition, args=macro_args, **extra)
                    if not macro.get("acl"):
                        apply_sharing(target, macro_sharing)
                    print(f"Macro '{macro_name}' actualizada correctamente.")
                else:
                    created = service.macros.create(name=macro_name, definition=macro_definition, args=macro_args,
                                                    sharing=macro_sharing, **extra)
                    existing.setdefault(macro_name, []).append(created)
                    print(f"Macro '{macro_name}' creada correctamente.")
            except Exception as e:
                print(f"Error al restaurar la macro '{macro_name}': {e}")

    except Exception as e:
        print(f"Error al restaurar macros: {e}")
