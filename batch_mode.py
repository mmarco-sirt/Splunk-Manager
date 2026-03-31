import os
from utils import connect_to_splunk
from options.alerts.backup_alerts import backup_alerts, backup_alerts_custom
from options.alerts.restore_alerts import restore_alerts, restore_alerts_from_file, list_backup_files

def batch_backup_alerts(clientes):
    print("\n=== Backup masivo de alertas ===")
    for client in clientes:
        print(f"\nConectando a {client['name']}...")
        service = connect_to_splunk(client)
        if not service:
            print(f"No se pudo conectar a {client['name']}. Continuando...")
            continue
        print(f"➡ Ejecutando backup en {client['name']}...")
        backup_alerts(service)

def batch_restore_alerts(clientes):
    print("\n=== Restore masivo de alertas ===")
    for client in clientes:
        print(f"\nConectando a {client['name']}...")
        service = connect_to_splunk(client)
        if not service:
            print(f"No se pudo conectar a {client['name']}. Continuando...")
            continue
        print(f"➡ Ejecutando restore en {client['name']}...")
        restore_alerts(service)

def multi_alert_backup_same_client(service):
    print("\n=== Backup múltiple en un solo cliente ===")
    alertas = input("Ingrese los nombres separados por coma: ")
    alertas = [a.strip() for a in alertas.split(",")]

    for alerta in alertas:
        print(f"➡ Backup de {alerta}...")
        backup_alerts_custom(service, alerta)

BACKUP_DIR = "backups/alertas"

def choose_multiple_backup_files():
    files = list_backup_files()

    if not files:
        print("No hay archivos de backup disponibles.")
        return []

    print("\nSeleccione los archivos de alertas:")
    for i, file in enumerate(files, start=1):
        print(f"{i}) {file}")

    while True:
        selection = input("Ingrese los números separados por coma (ej: 1,3): ")

        try:
            indexes = [int(x.strip()) - 1 for x in selection.split(",")]
            selected = []

            for idx in indexes:
                if 0 <= idx < len(files):
                    selected.append(
                        os.path.join(BACKUP_DIR, files[idx])
                    )
                else:
                    raise IndexError

            return selected

        except (ValueError, IndexError):
            print("Selección inválida. Intente nuevamente.")


def batch_restore_multiple_alerts_same_client(service):
    print("\n=== Restore múltiple de alertas en un cliente ===")

    selected_files = choose_multiple_backup_files()

    if not selected_files:
        return

    for backup_file in selected_files:
        print(f"\n➡ Procesando {os.path.basename(backup_file)}")
        restore_alerts_from_file(service, backup_file)

