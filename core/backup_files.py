import os

from core.paths import backup_dir


def list_backup_files(folder, prefix=""):
    """Lista los archivos .json de backups/<folder> que empiezan por 'prefix'."""
    dir_path = backup_dir(folder)
    return sorted(f for f in os.listdir(dir_path) if f.startswith(prefix) and f.endswith(".json"))


def choose_backup_file(folder, prefix="", label="backup"):
    """Permite al usuario elegir por consola un archivo de backups/<folder>. Devuelve la ruta o None."""
    files = list_backup_files(folder, prefix)
    if not files:
        print(f"No hay archivos de {label} disponibles.")
        return None

    print(f"Seleccione un archivo de {label} para restaurar:")
    for i, file in enumerate(files, start=1):
        print(f"{i}. {file}")

    while True:
        choice = input("Ingrese el número del archivo (vacío para cancelar): ").strip()
        if not choice:
            print("Operación cancelada.")
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(files):
            return os.path.join(backup_dir(folder), files[int(choice) - 1])
        print("Selección no válida, intente nuevamente.")


def choose_multiple_backup_files(folder, prefix="", label="backup"):
    """Permite elegir varios archivos de backups/<folder> separados por coma. Devuelve lista de rutas."""
    files = list_backup_files(folder, prefix)
    if not files:
        print(f"No hay archivos de {label} disponibles.")
        return []

    print(f"\nSeleccione los archivos de {label}:")
    for i, file in enumerate(files, start=1):
        print(f"{i}) {file}")

    while True:
        selection = input("Ingrese los números separados por coma (ej: 1,3) (vacío para cancelar): ").strip()
        if not selection:
            print("Operación cancelada.")
            return []
        try:
            indexes = [int(x.strip()) - 1 for x in selection.split(",")]
            if all(0 <= idx < len(files) for idx in indexes):
                return [os.path.join(backup_dir(folder), files[idx]) for idx in indexes]
        except ValueError:
            pass
        print("Selección inválida. Intente nuevamente.")
